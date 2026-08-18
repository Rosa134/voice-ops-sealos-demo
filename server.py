"""Voice robot operations feasibility demo.

This prototype intentionally uses only Python's standard library so it can be
packaged as one small Sealos container without a dependency build step.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(PROJECT_ROOT / "data")))
DB_PATH = DATA_DIR / "voice_ops_demo.sqlite3"
PORT = int(os.environ.get("PORT", "8080"))
INGEST_TOKEN = os.environ.get("DEMO_INGEST_TOKEN", "").strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def decode_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    enterprise_id TEXT NOT NULL,
    name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    enterprise_id TEXT NOT NULL,
    unique_id TEXT NOT NULL,
    analysis_run_id TEXT NOT NULL,
    call_time TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'voice',
    agent_name TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    task_status TEXT NOT NULL DEFAULT '',
    transfer_required INTEGER NOT NULL DEFAULT 0,
    transfer_executed INTEGER NOT NULL DEFAULT 0,
    termination_reason TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    barge_in_count INTEGER NOT NULL DEFAULT 0,
    silence_event_count INTEGER NOT NULL DEFAULT 0,
    ttfa_ms_avg INTEGER NOT NULL DEFAULT 0,
    final_emotion TEXT NOT NULL DEFAULT '',
    complaint_detected INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(project_id, unique_id, analysis_run_id)
);

CREATE TABLE IF NOT EXISTS quality_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    check_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    hit INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    tuning_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(call_id, check_id)
);

CREATE TABLE IF NOT EXISTS redline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    redline_id TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'high',
    hit INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    notification_status TEXT NOT NULL DEFAULT 'not_required',
    UNIQUE(call_id, redline_id)
);

CREATE TABLE IF NOT EXISTS badcases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    badcase_id TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'medium',
    expected TEXT NOT NULL DEFAULT '',
    observed TEXT NOT NULL DEFAULT '',
    owner_layer TEXT NOT NULL DEFAULT 'prompt',
    tuning_recommendation TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT NOT NULL DEFAULT 'postcall',
    created_at TEXT NOT NULL,
    UNIQUE(call_id, badcase_id)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()
        self.seed_if_empty()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self.lock, self.connect() as connection:
            connection.executescript(SCHEMA)

    def seed_if_empty(self) -> None:
        with self.lock, self.connect() as connection:
            if connection.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
                return
            projects = [
                ("qirui", "qirui", "奇瑞客户服务项目", "奇瑞售后语音机器人", "展示流程、工具和业务质检", now_iso()),
                ("demo-sales", "demo-enterprise", "销售邀约演示项目", "销售邀约语音机器人", "用于验证项目隔离", now_iso()),
            ]
            connection.executemany(
                "INSERT INTO projects(id, enterprise_id, name, agent_name, description, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                projects,
            )
            connection.commit()
        self.ingest("qirui", seed_payload_qirui(), allow_seed=True)
        self.ingest("demo-sales", seed_payload_sales(), allow_seed=True)

    def project(self, project_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()

    def projects(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM projects ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def ingest(self, project_id: str, payload: dict[str, Any], allow_seed: bool = False) -> dict[str, Any]:
        project = self.project(project_id)
        if not project:
            raise ValueError("project_not_found")
        if payload.get("project_id") != project_id:
            raise ValueError("project_id_mismatch")
        if payload.get("enterprise_id") != project["enterprise_id"]:
            raise ValueError("enterprise_id_mismatch")
        unique_id = str(payload.get("unique_id") or "").strip()
        analysis_run_id = str(payload.get("analysis_run_id") or "").strip()
        if not unique_id or not analysis_run_id:
            raise ValueError("unique_id_and_analysis_run_id_required")

        with self.lock, self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM calls WHERE project_id = ? AND unique_id = ? AND analysis_run_id = ?",
                (project_id, unique_id, analysis_run_id),
            ).fetchone()
            if existing and not allow_seed:
                return {"ok": True, "deduplicated": True, "call_id": existing["id"]}

            business = payload.get("business_result") or {}
            communication = payload.get("communication") or {}
            emotion = payload.get("emotion") or {}
            values = (
                project_id,
                project["enterprise_id"],
                unique_id,
                analysis_run_id,
                str(payload.get("call_time") or now_iso()),
                str(payload.get("channel") or "voice"),
                str(payload.get("agent_name") or project["agent_name"]),
                str(payload.get("summary") or ""),
                str(business.get("intent") or ""),
                str(business.get("task_status") or ""),
                int(bool(business.get("transfer_required", False))),
                int(bool(business.get("transfer_executed", False))),
                str(business.get("termination_reason") or ""),
                int(communication.get("duration_ms") or 0),
                int(communication.get("turn_count") or 0),
                int(communication.get("barge_in_count") or 0),
                int(communication.get("silence_event_count") or 0),
                int(communication.get("ttfa_ms_avg") or 0),
                str(emotion.get("final_emotion") or ""),
                int(bool(emotion.get("complaint_detected", False))),
                json_text(payload),
                now_iso(),
            )
            if existing:
                call_id = existing["id"]
                connection.execute(
                    """UPDATE calls SET call_time=?, channel=?, agent_name=?, summary=?, intent=?, task_status=?,
                    transfer_required=?, transfer_executed=?, termination_reason=?, duration_ms=?, turn_count=?,
                    barge_in_count=?, silence_event_count=?, ttfa_ms_avg=?, final_emotion=?, complaint_detected=?, raw_json=?
                    WHERE id=?""",
                    (values[4], values[5], values[6], values[7], values[8], values[9], values[10], values[11], values[12], values[13], values[14], values[15], values[16], values[17], values[18], values[19], values[20], call_id),
                )
                connection.execute("DELETE FROM quality_checks WHERE call_id = ?", (call_id,))
                connection.execute("DELETE FROM redline_events WHERE call_id = ?", (call_id,))
                connection.execute("DELETE FROM badcases WHERE call_id = ?", (call_id,))
            else:
                cursor = connection.execute(
                    """INSERT INTO calls(project_id, enterprise_id, unique_id, analysis_run_id, call_time, channel,
                    agent_name, summary, intent, task_status, transfer_required, transfer_executed, termination_reason,
                    duration_ms, turn_count, barge_in_count, silence_event_count, ttfa_ms_avg, final_emotion,
                    complaint_detected, raw_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
                call_id = cursor.lastrowid

            for item in payload.get("quality_checks") or []:
                connection.execute(
                    """INSERT INTO quality_checks(call_id, check_id, name, category, hit, reason, confidence, evidence_json, tuning_json)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        call_id,
                        str(item.get("check_id") or uuid.uuid4().hex[:10]),
                        str(item.get("name") or ""),
                        str(item.get("category") or ""),
                        int(bool(item.get("hit", False))),
                        str(item.get("reason") or ""),
                        float(item.get("confidence") or 0),
                        json_text(item.get("evidence") or []),
                        json_text(item.get("tuning") or {}),
                    ),
                )
            for item in payload.get("redlines", payload.get("redline_events", [])) or []:
                connection.execute(
                    """INSERT INTO redline_events(call_id, redline_id, severity, hit, reason, action, notification_status)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        call_id,
                        str(item.get("redline_id") or uuid.uuid4().hex[:10]),
                        str(item.get("severity") or "high"),
                        int(bool(item.get("hit", False))),
                        str(item.get("reason") or ""),
                        str(item.get("action") or ""),
                        str(item.get("notification_status") or "not_required"),
                    ),
                )
            for item in payload.get("badcases") or []:
                connection.execute(
                    """INSERT INTO badcases(call_id, badcase_id, category, subcategory, severity, expected, observed,
                    owner_layer, tuning_recommendation, status, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        call_id,
                        str(item.get("badcase_id") or uuid.uuid4().hex[:10]),
                        str(item.get("category") or ""),
                        str(item.get("subcategory") or ""),
                        str(item.get("severity") or "medium"),
                        str(item.get("expected") or ""),
                        str(item.get("observed") or ""),
                        str(item.get("owner_layer") or "prompt"),
                        str(item.get("tuning_recommendation") or item.get("tuning") or ""),
                        str(item.get("status") or "open"),
                        str(item.get("source") or "postcall"),
                        now_iso(),
                    ),
                )
            connection.commit()
            return {"ok": True, "deduplicated": False, "call_id": call_id}

    def overview(self, project_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            call_count = connection.execute("SELECT COUNT(*) AS n FROM calls WHERE project_id=?", (project_id,)).fetchone()["n"]
            redline_count = connection.execute("SELECT COUNT(*) AS n FROM redline_events r JOIN calls c ON c.id=r.call_id WHERE c.project_id=? AND r.hit=1", (project_id,)).fetchone()["n"]
            badcase_count = connection.execute("SELECT COUNT(*) AS n FROM badcases b JOIN calls c ON c.id=b.call_id WHERE c.project_id=?", (project_id,)).fetchone()["n"]
            open_badcase_count = connection.execute("SELECT COUNT(*) AS n FROM badcases b JOIN calls c ON c.id=b.call_id WHERE c.project_id=? AND b.status='open'", (project_id,)).fetchone()["n"]
            complaint_count = connection.execute("SELECT COUNT(*) AS n FROM calls WHERE project_id=? AND complaint_detected=1", (project_id,)).fetchone()["n"]
        return {"project_id": project_id, "call_count": call_count, "redline_count": redline_count, "badcase_count": badcase_count, "open_badcase_count": open_badcase_count, "complaint_count": complaint_count}

    def calls(self, project_id: str, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        limit = min(max(int((query.get("limit") or ["100"])[0]), 1), 500)
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM calls WHERE project_id=? ORDER BY call_time DESC, id DESC LIMIT ?", (project_id, limit)).fetchall()
        return [self.call_summary(row) for row in rows]

    @staticmethod
    def call_summary(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("transfer_required", "transfer_executed", "complaint_detected"):
            result[key] = bool(result[key])
        result.pop("raw_json", None)
        return result

    def call_detail(self, project_id: str, unique_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM calls WHERE project_id=? AND unique_id=? ORDER BY id DESC LIMIT 1", (project_id, unique_id)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["raw"] = decode_json(result.pop("raw_json", "{}"), {})
            for key in ("transfer_required", "transfer_executed", "complaint_detected"):
                result[key] = bool(result[key])
            result["quality_checks"] = [self._child(row, "quality") for row in connection.execute("SELECT * FROM quality_checks WHERE call_id=? ORDER BY id", (row["id"],)).fetchall()]
            for item in result["quality_checks"]:
                item["hit"] = bool(item["hit"])
                item["evidence"] = decode_json(item.pop("evidence_json", "[]"), [])
                item["tuning"] = decode_json(item.pop("tuning_json", "{}"), {})
            result["redlines"] = [self._child(row, "redline") for row in connection.execute("SELECT * FROM redline_events WHERE call_id=? ORDER BY id", (row["id"],)).fetchall()]
            for item in result["redlines"]:
                item["hit"] = bool(item["hit"])
            result["badcases"] = [self._child(row, "badcase") for row in connection.execute("SELECT * FROM badcases WHERE call_id=? ORDER BY id", (row["id"],)).fetchall()]
            return result

    @staticmethod
    def _child(row: sqlite3.Row, _kind: str) -> dict[str, Any]:
        return dict(row)

    def badcases(self, project_id: str, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        status = (query.get("status") or [""])[0]
        sql = """SELECT b.*, c.unique_id, c.call_time, c.intent FROM badcases b JOIN calls c ON c.id=b.call_id
                 WHERE c.project_id=?"""
        args: list[Any] = [project_id]
        if status:
            sql += " AND b.status=?"
            args.append(status)
        sql += " ORDER BY b.created_at DESC, b.id DESC LIMIT 500"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, args).fetchall()]


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "VoiceOpsDemo/0.1"

    @property
    def database(self) -> Database:
        return self.server.database  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{now_iso()}] {format % args}")

    def send_json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Demo-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path == "/":
            return self.serve_static("index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            name = path.removeprefix("/static/")
            content_type = {"css": "text/css; charset=utf-8", "js": "application/javascript; charset=utf-8"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
            return self.serve_static(name, content_type)
        if path == "/healthz":
            return self.send_json({"ok": True, "service": "voice-ops-demo"})
        if path == "/api/v1/projects":
            return self.send_json({"projects": self.database.projects()})
        parts = path.split("/")
        if len(parts) >= 6 and parts[:4] == ["", "api", "v1", "projects"]:
            project_id = parts[4]
            if not self.database.project(project_id):
                return self.send_json({"error": "project_not_found"}, HTTPStatus.NOT_FOUND)
            tail = parts[5:]
            if tail == ["overview"]:
                return self.send_json(self.database.overview(project_id))
            if tail == ["calls"]:
                return self.send_json({"calls": self.database.calls(project_id, query)})
            if len(tail) == 2 and tail[0] == "calls":
                detail = self.database.call_detail(project_id, tail[1])
                if detail is None:
                    return self.send_json({"error": "call_not_found"}, HTTPStatus.NOT_FOUND)
                return self.send_json(detail)
            if tail == ["badcases"]:
                return self.send_json({"badcases": self.database.badcases(project_id, query)})
        return self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/")
        parts = path.split("/")
        if len(parts) == 6 and parts[:4] == ["", "api", "v1", "projects"] and parts[5] == "postcall-results":
            project_id = parts[4]
            if INGEST_TOKEN and self.headers.get("X-Demo-Token", "") != INGEST_TOKEN:
                return self.send_json({"error": "invalid_demo_token"}, HTTPStatus.UNAUTHORIZED)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2_000_000:
                    raise ValueError("invalid_content_length")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("payload_must_be_object")
                return self.send_json(self.database.ingest(project_id, payload))
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def serve_static(self, name: str, content_type: str) -> None:
        target = (STATIC_ROOT / name).resolve()
        if STATIC_ROOT.resolve() not in target.parents:
            return self.send_json({"error": "invalid_static_path"}, HTTPStatus.BAD_REQUEST)
        if not target.exists():
            return self.send_json({"error": "static_file_not_found"}, HTTPStatus.NOT_FOUND)
        self.send_text(target.read_bytes(), content_type)


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], database: Database):
        super().__init__(address, DemoHandler)
        self.database = database


def seed_payload_qirui() -> dict[str, Any]:
    return {
        "project_id": "qirui",
        "enterprise_id": "qirui",
        "unique_id": "qirui-call-20260818-001",
        "analysis_run_id": "postcall-v001",
        "call_time": "2026-08-18T08:16:00+08:00",
        "agent_name": "奇瑞售后语音机器人",
        "summary": "用户咨询保养预约，机器人遗漏确认车型后直接调用预约工具。",
        "business_result": {"intent": "保养预约", "task_status": "部分完成", "transfer_required": False, "transfer_executed": False, "termination_reason": "用户主动结束"},
        "communication": {"duration_ms": 186000, "turn_count": 11, "barge_in_count": 1, "silence_event_count": 2, "ttfa_ms_avg": 780},
        "emotion": {"final_emotion": "neutral", "complaint_detected": False},
        "quality_checks": [
            {"check_id": "QC-INTENT-001", "name": "意图识别", "category": "intent_recognition", "hit": False, "reason": "正确识别为保养预约。", "confidence": 0.98, "evidence": ["用户：我想约下周保养"], "tuning": {"action": "保留", "suggestion": "继续覆盖预约类同义表达"}},
            {"check_id": "QC-FLOW-001", "name": "流程执行", "category": "workflow_execution", "hit": True, "reason": "按规则应先确认车型和车牌，再调用预约工具；实际跳过车型确认直接调用。", "confidence": 0.96, "evidence": ["节点 N03 未执行", "工具 reserve_service 在缺少 vehicle_model 时被调用"], "tuning": {"action": "优化流程守卫", "suggestion": "将车型确认设为 reserve_service 的前置条件，缺参时禁止调用"}},
            {"check_id": "QC-BIZ-001", "name": "业务信息完整性", "category": "business_accuracy", "hit": True, "reason": "预约请求缺少车型，工具参数不完整，无法保证预约门店资源准确。", "confidence": 0.93, "evidence": ["vehicle_model = null"], "tuning": {"action": "补充槽位校验", "suggestion": "调用工具前检查车型、手机号、期望时间三个必填变量"}},
            {"check_id": "QC-NAT-001", "name": "口语自然度", "category": "natural_language", "hit": False, "reason": "整体表达自然，无明显书面化或机械重复。", "confidence": 0.91, "evidence": ["确认话术符合口语习惯"], "tuning": {"action": "观察", "suggestion": "无需调整"}},
        ],
        "redlines": [
            {"redline_id": "RL-FLOW-001", "severity": "high", "hit": True, "reason": "跳过必经节点并调用预约工具。", "action": "推送钉钉群并进入 Badcase 队列", "notification_status": "pending_dingtalk"},
            {"redline_id": "RL-DATA-001", "severity": "medium", "hit": False, "reason": "未发现隐私信息外泄。", "action": "无需推送", "notification_status": "not_required"},
        ],
        "badcases": [
            {"badcase_id": "BC-20260818-001", "category": "workflow_execution", "subcategory": "required_node_skipped", "severity": "high", "expected": "确认车型后才能调用 reserve_service。", "observed": "跳过车型确认，直接调用 reserve_service。", "owner_layer": "workflow", "tuning_recommendation": "新增工具前置条件和流程状态机断言；缺少 vehicle_model 时回到车型确认节点。", "status": "open", "source": "postcall"},
        ],
    }


def seed_payload_sales() -> dict[str, Any]:
    return {
        "project_id": "demo-sales",
        "enterprise_id": "demo-enterprise",
        "unique_id": "sales-call-20260818-001",
        "analysis_run_id": "postcall-v001",
        "call_time": "2026-08-18T09:05:00+08:00",
        "agent_name": "销售邀约语音机器人",
        "summary": "用于验证不同项目之间的数据隔离。",
        "business_result": {"intent": "课程咨询", "task_status": "已完成", "transfer_required": False, "transfer_executed": False, "termination_reason": "正常结束"},
        "communication": {"duration_ms": 92000, "turn_count": 7, "barge_in_count": 0, "silence_event_count": 1, "ttfa_ms_avg": 620},
        "emotion": {"final_emotion": "positive", "complaint_detected": False},
        "quality_checks": [{"check_id": "QC-SALES-001", "name": "意图识别", "category": "intent_recognition", "hit": False, "reason": "正确识别课程咨询。", "confidence": 0.97, "evidence": ["用户询问课程价格"], "tuning": {"action": "保留", "suggestion": "无需调整"}}],
        "redlines": [],
        "badcases": [],
    }


def main() -> None:
    database = Database(DB_PATH)
    server = DemoServer(("0.0.0.0", PORT), database)
    print(f"Voice Ops Demo listening on http://0.0.0.0:{PORT} (db={DB_PATH})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Voice Ops Demo")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
