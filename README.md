# voice-ops-sealos-demo

这是语音机器人运营 AI 检测平台的 Sealos Cloud 可行性 Demo。它把“话后助手推送的质检 JSON”接收进 SQLite，再通过一个浏览器看板展示项目隔离、逐通电话指标、红线和 Badcase 调优建议。

## 本地启动

```powershell
C:\Python311\python.exe server.py
```

浏览器打开 <http://127.0.0.1:8080>。首次启动会自动创建 `data/voice_ops_demo.sqlite3` 并写入奇瑞、销售邀约两个项目的演示数据。

也可以使用 Docker Compose：

```powershell
docker compose up --build
```

## HTTP 接口

```text
GET  /healthz
GET  /api/v1/projects
GET  /api/v1/projects/{project_id}/overview
GET  /api/v1/projects/{project_id}/calls
GET  /api/v1/projects/{project_id}/calls/{unique_id}
GET  /api/v1/projects/{project_id}/badcases?status=open
POST /api/v1/projects/{project_id}/postcall-results
```

POST body 需要至少包含 `project_id`、`enterprise_id`、`unique_id`、`analysis_run_id`。质检项格式为：

```json
{
  "check_id": "QC-FLOW-001",
  "name": "流程执行",
  "category": "workflow_execution",
  "hit": true,
  "reason": "跳过必经节点后直接调用工具",
  "evidence": ["节点 N03 未执行"],
  "tuning": {"action": "优化流程守卫", "suggestion": "缺参禁止调用"}
}
```

重复推送相同 `project_id + unique_id + analysis_run_id` 会返回 `deduplicated=true`，不会新增重复通话。

本地模拟推送：

```powershell
C:\Python311\python.exe scripts\simulate_dify_push.py
```

## Sealos Cloud 部署

1. 将本目录上传到 DevBox，或构建并推送 `Dockerfile` 对应的 OCI 镜像。
2. 新建应用时打开“公网访问”，容器端口填 `8080`。
3. 设置环境变量 `PORT=8080`、`DATA_DIR=/data`；为 `/data` 绑定持久化卷，否则重启会丢失 Demo 数据。
4. 通过 Sealos 自动分配的公网域名打开看板，并用该域名替换模拟推送脚本的 `base_url`。
5. 生产化时把 SQLite 换成 Sealos 托管 PostgreSQL，并补充统一登录、项目成员权限、真实钉钉机器人、签名校验、审计和备份。

## 说明

这是“部署与链路验证”原型，不是生产版。统一登录、Dify、钉钉、Codex 调优发布目前以接口和数据结构预留为主。
