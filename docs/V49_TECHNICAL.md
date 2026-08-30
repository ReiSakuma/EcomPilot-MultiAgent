# V49 批次执行回执与断线恢复技术文档

## 目标

v48 已经把批次店铺写入放进持久化队列，但用户页面只在当前 JavaScript 调用栈中保存 `runtime_job_id`。刷新页面、关闭标签页或切换会话后，后台任务仍会执行，页面却不知道该查询哪个 Job。v49 将 Runtime Job 作为持久化执行回执，并建立重新发现协议。

## 回执协议

`BatchExecutionJobStatus` 新增：

- `execution_generations`：派发时每个商品的执行代数。
- `created_at`、`updated_at`：排队和最近状态变更时间。
- `is_latest`：该 Job 是否仍是批次的最新一轮操作。

Runtime 提供租户强制过滤的 `list_jobs()`。`BatchExecutionDispatcher.latest()` 只查询 `batch_store_execution` 类型和指定批次的幂等键前缀，不扫描或暴露其他租户任务。

## 恢复流程

```text
打开历史会话或刷新页面
  -> 从 requirements 面板取得 batch_job_id
  -> GET /api/copilot/batches/{batch_job_id}/executions/latest
  -> 无回执：保持普通方案页面
  -> queued/leased：接管原 runtime_job_id 并继续轮询
  -> completed：恢复 BatchExecutionReport
  -> failed/dead：展示公开错误，不重复写店
```

## 防止结果串线

前端维护恢复代号 `batchRecoveryEpoch`。切换会话、创建新会话或发送新任务时会递增代号并取消旧轮询。每次状态查询还必须同时满足：

1. Runtime Job ID 仍是当前轮询对象。
2. 当前页面仍展示同一个 `batch_job_id`。
3. 服务端返回 `is_latest=true`。

任意条件不满足时，旧回执只保留在审计数据库中，不能更新当前工作区。

## 幂等与恢复边界

v49 不重新执行已经完成的 Job，而是读取其持久化 `BatchExecutionReport`。如果 Worker 仍在执行，页面只恢复观察，不创建第二个 Job。真正的新人工重试必须让商品执行代数增加，沿用 v48 的逻辑幂等协议。

SQLite WAL 仍是单机多进程参考实现；跨主机部署需要把同一回执检索协议迁移到共享数据库或工作流平台。

## API

```http
GET /api/copilot/batches/{batch_job_id}/executions/latest
GET /api/copilot/batch-executions/{runtime_job_id}
```

两个接口都按可信身份中的 `tenant_id` 过滤，并要求只读任务权限。

## 验收

```bash
pytest -q tests/test_v49_batch_receipt_recovery.py
python scripts/run_v49_acceptance.py
pytest -q
```
