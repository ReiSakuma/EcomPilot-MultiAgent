# v56 促销类型、单位与旧协议迁移

## 1. 版本目标

v56 解决 Strategy 链路长期存在的单位歧义：模型层叫 `selected_discount`，工具层叫 `discount`，业务结果又叫 `coupon`，这些裸数字无法说明是“元”“折扣比例”还是“支付比例”。本版先统一语言和数据协议，不提前实现 v57 的动态候选策略。

## 2. 新增能力

### 2.1 PromotionSpec 1.0

`app/model/contracts.py` 新增以 `promotion_type` 为判别字段的 Pydantic 联合类型：

- `none`：无优惠。
- `fixed_amount_coupon`：固定金额券，只能携带 `discount_amount_yuan`。
- `percentage_discount`：百分比折扣，只能携带 `discount_rate`；10% 优惠写为 `0.10`，九折也规范化为优惠 `0.10`，不会误写成 90% 优惠。
- `gift`：赠品，只携带赠品名称和数量。
- `bundle`：组合促销，只携带组合数量和组合价。

所有类型固定记录 `protocol_version=1.0` 和 `currency=CNY`。Pydantic 使用 `extra=forbid`，因此固定金额券混入百分比字段会立即失败。

### 2.2 数字所有权

模型可以声明促销类型和参数，但不能自己决定毛利结果。`calculate_margin` 和 `simulate_discount_scenarios` 的新 Schema 只向模型暴露 `discount_amount_yuan` 或 `candidate_discount_amounts_yuan`，到手价、毛利额和毛利率仍由确定性 Python 工具计算。

Strategy 结果中的 `promotion` 是新的可信合同；`coupon` 仅是给现有模拟商家后台使用的兼容投影。二者必须与工具输出中的 `discount_amount_yuan` 一致。

### 2.3 旧数据只读迁移

`app/model/promotion_migration.py` 不修改磁盘上的旧 Checkpoint，而是在加载时创建内存副本：

- 旧 `coupon` 按历史合同解释为人民币固定金额。
- 带 `discount_unit=yuan/percent` 的旧字段按明确单位迁移。
- 只有裸 `discount` 且没有单位时返回 `requires_regeneration`，不会猜测。
- 迁移状态写入 `TaskState.protocol_migrations`；旧 Strategy Artifact 重算内容哈希，原文件保持不变。

### 2.4 可观测证据

- Strategy Artifact 写入 `promotion_protocol_version` 和完整 `promotion`。
- Agent 完成 Trace 的 Artifact 摘要写入促销协议版本。
- Run Bundle 顶层写入 `promotion_protocol_version`，任务状态中保留完整促销合同和迁移审计。

## 3. 兼容边界

旧 ReAct 记录中的 `selected_discount`、`discount` 和 `candidate_discounts` 可以按已知 v55 合同读取；新 JSON Schema 不再向模型暴露这些字段。Seller Center 当前仍消费 `coupon`，它由规范促销合同确定性投影生成，不再由模型自由填写。

## 4. 验证

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v56
python scripts/run_v56_acceptance.py
python -m pytest -q tests/test_v56_promotion_contracts.py
python -m pytest -q
```

重点测试覆盖 10 元券、10% 优惠、九折、非法字段组合、明确单位迁移、歧义拒绝、只读 Checkpoint 迁移、序列化往返和完整工作流兼容。

## 5. 本版边界

v56 仍沿用上一版的单一安全优惠行为。根据商品和市场动态提出 2 至 4 个候选、逐个工具评估、淘汰失败候选和受控重选属于 v57，不在本版伪装实现。
