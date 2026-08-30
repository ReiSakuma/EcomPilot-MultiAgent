# V46 安全批次确认与执行技术文档

## 版本目标

v45 能为多个商品分别生成方案，但只能让每个商品独立等待确认。v46 增加一个受控的批次执行入口：用户先勾选具体商品，再由系统逐项消费各自的审批状态并写入模拟店铺。

批次按钮只是操作入口，不是绕过子任务安全机制的“总审批令牌”。

## 主流程

```text
已生成的 BatchJob
  -> 用户显式勾选 1 至 5 个 awaiting_approval 子项
  -> API 校验租户与 task.approve 权限
  -> 原子抢占一个 BatchItem
  -> 校验子任务 Checkpoint 版本
  -> 复用单任务 approve()
       -> 策略授权
       -> Playwright/Mock Browser 执行
       -> 店铺回读验证
  -> 持久化 completed 或 needs_attention
  -> 串行处理下一个已选子项
  -> 汇总并回写原会话响应
```

## 为什么生成并发、写入串行

- 方案生成是只读或草稿计算，v45 保留最多 2 路并发以降低等待时间。
- 店铺同步会修改商品、库存和促销，是破坏性操作。v46 固定串行执行，避免两个 Browser Agent 同时覆盖店铺状态。
- 每批最多 5 项，限制一条消息带来的模型、工具和店铺写入放大倍数。

## 安全与稳定性

- **显式选择**：未勾选的商品保持 `awaiting_approval`，不会被执行。
- **子任务审批**：每项仍校验自己的 Checkpoint 与执行计划，不共享模糊的批次审批。
- **原子抢占**：SQLite 条件更新只允许一个请求把子项从 `awaiting_approval` 改为 `running`；并发重复请求得到冲突。
- **幂等复用**：已完成项再次确认时直接返回原结果，不重新调用浏览器。
- **失败隔离**：单项异常记录为 `needs_attention`，此前成功项不回滚，其他已选项继续执行。
- **租户隔离**：批次、子项、会话与 Checkpoint 都校验 `tenant_id`。
- **持久化投影**：执行汇总更新原 Turn 的 `response_payload`，页面刷新不会恢复成旧的等待状态。

## 状态语义

- `completed_count`：成功生成方案的子项数，延续 v45 语义。
- `executed_count`：已经通过浏览器执行并回读验证的子项数。
- `failed_count`：执行失败或需要人工处理的子项数。
- `awaiting_approval`：仍有未选择或未执行子项。
- `partially_completed`：至少一项完成且至少一项失败。
- `completed`：所有子项都完成店铺同步。
- `failed`：没有成功项且已无可继续审批项。

## 用户界面

批次方案生成后，结果顶部显示可勾选商品。按钮随选择数量更新为“确认并同步 N 个商品”。执行后立即显示“批次店铺同步结果”，并同步写入会话快照供刷新和历史追踪。

## API

```http
POST /api/copilot/batches/{batch_job_id}/approve
Content-Type: application/json

{
  "item_ids": ["item_01", "item_02"],
  "expected_checkpoint_versions": {
    "item_01": 7,
    "item_02": 6
  }
}
```

## 版本边界

v46 针对模拟 Seller Center 提供安全的多商品写入控制。它没有实现真实电商平台的分布式事务；接入真实平台时还需要平台侧幂等键、库存版本号、补偿任务和持久化队列。

## 验收

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v46
pytest -q
python scripts/run_v46_acceptance.py
```
