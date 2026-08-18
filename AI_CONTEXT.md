# AI_CONTEXT — voice-ops-sealos-demo

## 目标

验证“话后助手 HTTP 推送 → 单容器服务接收 → SQLite 持久化 → 项目隔离看板 → 通话/质检/红线/Badcase 详情”是否能在 Sealos Cloud 上跑通。

## 已确认设计

- 单容器、Python 标准库实现，监听 `0.0.0.0:8080`。
- SQLite 数据文件位于 `DATA_DIR`，Sealos 使用持久化目录 `/data`。
- 登录在 Demo 中为统一入口的演示态，不实现真实账号体系。
- 企业隔离模型：`enterprise_id` 对应项目，所有查询和写入都带 `project_id`。
- Qirui 作为示例项目，展示每通电话的质检指标、红线和 Badcase。
- 质检指标保留 `hit`、`reason`、`evidence`、`tuning`。
- 流程错误单列为 `workflow_execution`，工具错误和变量状态错误分列。

## 范围

本 Demo 包含：项目切换、概览计数、通话列表、通话详情、Badcase 队列、红线列表、HTTP 入库、SQLite 去重、容器化和 Sealos 部署说明。

## 非范围

真实 SSO、生产权限模型、钉钉真实推送、Dify 真实连接、PostgreSQL 高可用、加密审计、Codex 自动改提示词发布。

## 验收标准

1. `GET /healthz` 返回 `ok=true`。
2. 两个项目可切换，项目 A 的数据不会出现在项目 B。
3. 推送一条符合话后分析契约的 JSON 后，可在通话详情看到 `quality_checks`、红线和 Badcase。
4. 同一 `unique_id + analysis_run_id` 重复推送不重复插入。
5. Docker 容器默认监听 8080，并可将 SQLite 数据目录挂载到 `/data`。

## 验证记录

- `python -m unittest discover -s tests -v`：2 个测试通过。
- `GET /healthz`：200，`ok=true`。
- 看板资源 `/`、`/static/app.js`、`/static/styles.css`：200。
- 项目列表：2 个项目；Qirui 与销售邀约数据互不可见。
- 同一 `unique_id + analysis_run_id` 二次推送：返回 `deduplicated=true`。
- Qirui 详情：包含 4 个质检项、2 个红线、1 个 Badcase；流程项分类为 `workflow_execution`。
- 本机没有安装 Docker，因此未执行本地镜像构建；Dockerfile 已完成静态检查。

## 计划确认

用户已确认“开始做 Demo”，本文件作为执行阶段的范围锁定记录。
