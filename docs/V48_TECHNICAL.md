# V48 持久化批次执行队列技术文档

## 目标

v47 已经定义了安全恢复语义，但批次执行仍由一次 HTTP 请求同步承载。v48 将批次首次确认和失败重试统一派发到已有 `DistributedRuntime`，让队列、租约和 Worker 管理执行生命周期。

## 架构

```text
用户勾选商品
  -> POST batch dispatch
  -> 权限与 BatchItem 状态检查
  -> Durable Runtime enqueue(browser pool)
  -> 立即返回 runtime_job_id
  -> Browser Worker lease job
  -> BatchExecutionService
  -> 子任务审批 / 恢复 / 浏览器执行 / 回读
  -> Runtime complete 或 retry/dead
  -> UI 按 runtime_job_id 查询并展示最终报告
```

`BatchExecutionDispatcher` 只做队列协议转换，不拥有业务审批规则。Worker 收到强类型 `BatchExecutionJobRequest` 后仍调用 v47 的 `BatchExecutionService`。

## 幂等语义

逻辑键由以下字段生成：

- `batch_job_id`
- `operation`（approve 或 retry）
- 排序后的 `item_ids`
- 每个商品当前 `execution_attempts`

相同逻辑操作即使页面刷新并产生新的客户端请求 ID，也只返回原 Job。失败项完成一次尝试后 execution generation 增加，下一次人工重试会得到新 Job。

同一逻辑键若携带不同 Checkpoint 版本，Runtime 拒绝为 `RuntimeIdempotencyConflict`，避免偷偷替换已经排队的计划。

## Worker 可靠性

- 使用 `browser` Bulkhead，与模型、SQL 和普通 Workflow 隔离。
- Job 最多执行 3 次；技术异常时重新排队，耗尽后进入 `dead`。
- Worker 通过租约和 `lease_token` 持有任务；过期 Worker 不能提交结果。
- BatchExecutionService 自身仍提供子项原子抢占和已完成项幂等，因此 Worker 重放不会重复写店。
- 单个商品业务失败会形成正常的批次报告，不触发整个 Runtime Job 的盲目重试。

## API

```http
POST /api/copilot/batches/{batch_job_id}/dispatch
GET  /api/copilot/batch-executions/{runtime_job_id}
```

派发请求包含操作、选中商品、Checkpoint 版本和客户端请求 ID；服务端补入可信 Principal 和 execution generation。

## 前端

用户点击确认或重试后，页面显示 Job ID，并每 500ms 查询状态：

- `queued`：等待 Browser Worker。
- `leased`：Worker 正在执行，并展示第几次尝试。
- `completed`：读取 `BatchExecutionReport` 并更新会话与结果面板。
- `dead`：展示公开错误，提示从运维后台查看。

## 边界

本版使用 SQLite WAL 作为面试项目的多 Worker 参考实现。生产部署应将相同协议映射到 PostgreSQL/Redis 或持久工作流平台，并将 API 内的兼容 Worker 拆为独立进程。

## 验收

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v48
pytest -q tests/test_v48_batch_dispatch.py
pytest -q
python scripts/run_v48_acceptance.py
```
