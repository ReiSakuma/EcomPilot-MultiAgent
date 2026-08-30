# V47 批次失败恢复与重试审计技术文档

## 目标

v47 解决 v46 中 `needs_attention` 子项只能停留、不能安全恢复的问题。恢复不是无条件重放，而是一个受 Checkpoint、写状态和尝试预算约束的新执行尝试。

## 恢复流程

```text
用户选择失败商品
  -> 校验租户与审批权限
  -> 读取 BatchItem 与独立 Checkpoint
  -> 检查是否存在 unknown side-effect
  -> 检查 execution_attempts < 3
  -> needs_attention -> awaiting_approval
  -> 原子抢占并增加尝试次数
  -> 复用单任务 Recovery/Approval/Browser 流程
  -> 回读验证
  -> completed 或 needs_attention
  -> 追加 execution_history
```

## 关键规则

- 每个子项最多执行 3 次，首次执行也计入。
- 已完成子项继续采用幂等复用，不进入恢复。
- `tool_records` 中存在副作用工具且状态为 `unknown` 时，直接返回 `batch_unknown_write_requires_reconciliation`。
- 达到上限返回 `batch_retry_exhausted`，保留在人工处理状态。
- 每次尝试保存 attempt、status、error_code 和 occurred_at；历史采用追加写，不覆盖旧错误。
- 重试一个商品不重跑其他商品的 Market、Listing、Strategy 或 Browser。

## 数据库迁移

Conversation Schema 升级到 13，`batch_items` 新增：

- `execution_attempts`：已经真正发起的店铺执行次数。
- `execution_history`：逐次执行审计列表。

## API 与界面

```http
POST /api/copilot/batches/{batch_job_id}/retry
```

用户界面将首次审批项和失败恢复项分组显示。失败项展示“已尝试 N/3 次”，用户明确勾选后才会恢复。

## 边界

v47 的执行仍发生在当前 HTTP 服务进程中。请求断连和多 Worker 租约恢复将在 v48 通过持久化队列解决。

## 验收

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v47
pytest -q tests/test_v47_batch_recovery.py
pytest -q
python scripts/run_v47_acceptance.py
```
