# EcomPilot v34.1 技术文档

## 1. 版本目标

v34 将前七个版本形成的会话、商品、Agent、Memory 和权限能力包装成一个统一产品。它没有改变业务 Agent 的职责，也没有引入新的自由规划器；本版主要解决用户无法理解执行过程、页面刷新后状态不一致、审批对象不明确和移动端难用的问题。

## 2. 整体结构

```text
用户消息
  -> POST /api/copilot/messages/dispatch
  -> 创建持久 stream_id
  -> 后台执行 ConversationFacade / Route Plan / Multi-Agent Workflow
  -> CopilotEventStore 写入公开进度事件
  -> SSE 按 event_id 推送
  -> response_ready 携带 CopilotResponse 1.6
  -> panels 驱动右侧工作台
```

同步 API 仍然保留，便于测试脚本和旧客户端迁移。用户 UI 只使用异步入口。

## 3. 实时事件

事件保存在 SQLite 的 `copilot_streams` 和 `copilot_events` 表中。事件带租户、递增 `event_id`、阶段、状态和任务引用。浏览器断线后可通过 `Last-Event-ID` 或 `after` 参数继续读取，因而完成状态不依赖 JavaScript 内存。

公开事件只投影已记录的业务事实，例如 Agent 开始/完成、模型返回、工具完成、审核修订和执行完成；不会写入或展示提示词、SQL、工具参数、模型原文或隐藏推理。终态必定收敛到 `response_ready` 或 `stream_failed`。

v34.1 在 `TraceRecorder` 的统一出口做白名单投影，并用 `ContextVar` 将当前 stream 的事件接收器传入 Agent 线程池和工具线程池。因此事件在实际执行期间落库，不再等到最终响应后集中补写。事件写入异常被隔离为可观测性故障，不会反向终止业务任务。

## 4. 动态面板合同

`CopilotResponse.protocol_version` 升级到 `1.6`。UI 只根据 `panels[].panel_id` 和 `status` 决定展示哪些业务面板，不读取 TaskState 的任意内部字段来猜测任务类型。历史会话恢复时，消息和最新 `CopilotResponse` 同时从服务端加载。会话 URL 保存 `conversation_id`；刷新后，页面调用活动流查询接口，如果本轮仍在运行，就从服务端持久事件重新播放并继续订阅。

支持面板包括 Listing、Strategy、Market、Performance/Analytics、Review、Execution、Product 和 Timeline。只读市场问题不会出现 Listing 或审批按钮；上新任务会展示方案和确认状态。

## 5. 审批一致性

等待确认的响应会对 `task_id` 以及 Listing、Strategy、Review 三个面板的规范化内容计算 SHA-256，得到 `execution_plan_hash`。页面上的消息确认按钮和右侧确认按钮都提交同一个哈希。服务端发现哈希与当前 Checkpoint 不一致时返回 409，要求刷新后确认最新方案。

## 6. 历史会话与移动端

会话查询由后端执行租户过滤，并支持标题/消息搜索、按 `product_id` 过滤、只查看最新任务处于 `awaiting_approval` 的会话。桌面端保持历史、对话、结果三栏；窄屏采用“对话 / 结果”双标签。

## 7. 运维与用户边界

用户页展示结论、数据来源、执行步骤和本任务实际模型调用次数，不展示 Raw JSON、Token、权限令牌或隐藏推理。运维页保持只读，展示 Route Plan、Context/Memory 引用、模型记录、工具、权限、A2A、Sandbox 和 Execution 证据。

页面严格区分可用服务、本任务实际使用服务和测试桩。确定性模型或 Mock Browser 不能标记成真实调用。

## 8. 数据库迁移

Schema 版本升到 6，新增 `copilot_streams` 和 `copilot_events`。所有读取均使用 `tenant_id`，事件流不存在时统一返回 404。已有 v33 数据结构不变。

## 9. 当前边界

- v34 的 Agent 执行仍是进程内后台线程，适合单机 Demo，不等同于生产任务队列。
- 页面刷新可恢复同一服务进程内仍在运行的任务；进程意外退出后的任务接管需要生产级队列和 Worker 租约，尚未实现。
- 高并发队列、分布式锁、Outbox 和混沌恢复属于后续 v36-v39。
- 不在用户页面暴露 Raw JSON、Token 明细或 Capability Token。

## 10. v34.1 稳定化变更

- 执行中的 Agent、模型、工具和语义修正会即时写入持久 SSE。
- 公开事件采用字段白名单，只包含阶段、状态、用户可读说明和 Trace 引用。
- 新增 `GET /api/copilot/conversations/{conversation_id}/active-stream`。
- 页面刷新后可恢复活动任务，切换或新建会话会关闭旧连接。
- 当前服务配置与本任务实际模型调用分别展示，测试桩不会冒充真实模型。
- 补齐 `analytics_agent` 的真实模型与 ReAct 运行时白名单，使 DeepSeek 严格启动配置可达。
