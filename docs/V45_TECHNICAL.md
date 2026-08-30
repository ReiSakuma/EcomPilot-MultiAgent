# V45 有界批处理与部分失败隔离技术文档

## 目标

v45 承接 v44 已确认的 `BatchPlan`，为每个商品分别运行方案工作流，并返回一个批次汇总。它优先保证故障边界清晰，不追求一次性把整批商品写入店铺。

## 执行结构

```text
BatchPlan
  -> BoundedBatchOrchestrator (max_workers=2, max_items=5)
       -> child TaskSession A -> TaskState A -> awaiting_approval
       -> child TaskSession B -> TaskState B -> failed
  -> BatchRunReport (partially_completed)
```

每个子项都有独立的 `task_session_id`、`task_id`、`run_id` 和 Checkpoint。批次只保存索引和统计，不作为共享可变 TaskState。

## 稳定性策略

- **有界并发**：单批最多 5 项，同时最多运行 2 项，避免一条用户消息无限放大模型调用。
- **失败隔离**：一个子任务发生模型、工具或协议异常时，只将该 `batch_item` 标记为 failed，其他 Future 继续收敛。
- **幂等重放**：已经产生 `task_id` 且处于 awaiting_approval/completed 的子项直接从 Checkpoint 复用，不再次调用模型和工具。
- **租户隔离**：批次、子项和 TaskSession 的读取及更新都带 `tenant_id`；绑定结果时还校验预创建的子任务身份。
- **独立审批**：v45 不伪造一个覆盖整批的审批令牌。成功子项分别等待确认，避免“一次点击”意外执行多个写操作。

## 汇总状态

- `awaiting_approval`：所有子项方案都成功生成。
- `partially_completed`：至少一个成功、至少一个失败或跳过。
- `failed`：没有任何子项成功。

`completed_count` 表示已经成功生成方案的子项数；`failed_count` 表示失败或因缺字段跳过的子项数。

## 版本边界

v45 生成独立方案，但不会自动批量写店。批量审批策略、逐项补充缺失字段和批量执行补偿属于后续能力；当前默认逐项审批是更保守的安全边界。

## 验收

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v45
pytest -q
python scripts/run_v45_acceptance.py
```
