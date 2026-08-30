# EcomPilot MultiAgent v35 技术文档

## 版本目标

v35 不继续扩张业务功能，而是把 v27-v34 已完成的会话、记忆、动态路由、多 Agent、ReAct、
SQL 安全、审批和浏览器执行收束为可验收的面试 MVP。核心原则是协议统一、指标可量化、证据可追溯、边界不夸大。

## 主干架构

```text
用户工作台
  -> RequestCompiler（意图与明确字段）
  -> ConversationOrchestrator（白名单 RoutePlan）
  -> LangGraph 会话图
  -> Market / Listing / Strategy / Review / Analytics Agent
  -> Policy Gateway / SQL Sandbox / Approval Gate
  -> Browser Agent -> Mock Seller Center
  -> Conversation、Product Ledger、Trace、Artifact 与 Memory
```

模型可以在 Agent 内选择被授权的工具；程序负责租户、能力范围、步骤预算、结构校验和副作用审批。
严格 DAG 管理 Agent 之间的依赖，有限 ReAct Loop 只存在于单个节点内部，并有步数、重复调用和超时终止条件。

## 协议统一

`app/release/protocols.py` 是跨模块契约版本的单一入口。`GET /api/release/protocols` 暴露：

- Conversation Database schema 6
- TaskState 1.0
- CopilotResponse 1.6
- RoutePlan 1.0
- Handoff 1.1
- Artifact、Failure、A2A、Sandbox 1.0
- Run Bundle 2.0

持久化协议使用迁移兼容；新增状态字段必须有默认值；安全边界协议使用严格校验。

## MVP 质量门禁

`python scripts/run_v35_mvp_gate.py` 使用冻结数据集计算意图、字段、实体、路由、数值事实、写安全、
租户隔离、历史恢复、危险 SQL 和结构化协议指标。它还验证并发重复提交、幂等冲突、数据库迁移和损坏 Checkpoint。
离线门禁不依赖外部 API，真实 DeepSeek 与 Playwright 仍使用各自 smoke test 验证。
Release Readiness 还会读取桌面与移动端 Playwright 视觉报告；两个视口、控制台和溢出检查均通过后才显示面试就绪。

## Run Bundle v2

`scripts/export_run_bundle.py` 导出一次任务所属会话的消息、回答、任务状态、RoutePlan、模型与工具记录、
A2A、安全账本、审批、商品、记忆、Trace 和截图。`bundle_manifest.json` 对所有条目记录 SHA-256，便于离线检查是否被修改。

## 本版修复

质量门禁发现精确商品名会被宽泛类别别名稀释。v35 调整实体解析优先级：先解析精确别名，再进行模糊包含；
多个精确或模糊候选仍返回 `ambiguous`，绝不静默猜选。

## 声明边界

这是面试 MVP，不声明生产就绪。静态演示身份、本地 SQLite/Checkpoint、进程沙盒和 Mock Seller Center
需要在生产部署前替换为企业身份、共享事务存储、队列与分布式锁、强隔离沙盒和真实平台连接器。
