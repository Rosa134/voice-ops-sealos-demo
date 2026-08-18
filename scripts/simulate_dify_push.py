"""Send one postcall analysis payload to the local Demo ingestion endpoint."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import seed_payload_qirui  # noqa: E402


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    payload = seed_payload_qirui()
    payload["unique_id"] = "qirui-simulated-20260818-001"
    payload["analysis_run_id"] = "simulated-run-v001"
    payload["summary"] = "模拟 Dify 话后助手推送：验证 HTTP 入库与项目隔离。"
    request = urllib.request.Request(
        f"{base_url}/api/v1/projects/qirui/postcall-results",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
