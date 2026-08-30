# v63 候选强制收尾与重试收敛

## 版本目标

v63 解决的不是“让模型永远输出正确”，而是让一次可恢复的模型错误不再拖垮整张商品上新任务。Strategy 仍负责根据品类、市场和运营目标提出候选，但候选资格、调用上限、重试次数和最终停止条件由程序治理。

## 主流程

```text
可信任务字段
  -> EvidencePlan（0 至 2 个可选证据工具）
  -> 框架执行并生成 EvidenceLedger
  -> 模型生成 2 至 4 个候选
  -> 确定性工具核算毛利、库存、预算和促销单位
  -> 有合格候选
       -> 模型选择成功：使用模型选择
       -> 唯一合格：不调用模型，直接选择
       -> 截断/超时/非法 ID/预算耗尽：确定性强制收尾
  -> 没有合格候选
       -> 最多一次候选修复
       -> 仍无合格候选才要求用户调整条件
  -> Review
```

## 四次逻辑调用硬预算

`LogicalModelCallBudget` 将整个 Strategy 候选流程限制为最多 4 次逻辑调用。一次逻辑调用可以由 Provider 在网络层按配置重试，但不会被重复计为多个 Strategy 决策。

典型正常路径为 3 次：证据计划、候选生成、候选选择。第四次只能被以下一种恢复动作使用：

1. JSON Schema 修复；
2. 全部候选不合格后的候选修复；
3. 候选 ID 不存在或不合格后的紧凑重选。

如果第四次已经被别的恢复动作使用，候选选择不再继续调用模型，而是从合格候选中确定性收尾。Trace 中每次调用都记录 `retry_owner` 和 `retry_kind`。

## 重试所有权

- `ModelAdapter`：网络超时、429、可恢复 5xx 等传输重试。
- `structured_output_layer`：最多一次 JSON 结构修复。
- `strategy_agent`：最多一次候选语义修复或非法 ID 重选。
- `Workflow`：不对上述已经消费过重试预算的失败再次重跑整个 Strategy。

这样避免同一个错误被四层组件各重试一次，造成调用暴涨。

## 确定性强制收尾

强制收尾只看已经通过 `evaluate_strategy_candidates` 的候选。排序信息包括证据引用数量、预计总毛利、促销成本和稳定候选 ID。排序结果写入 `candidate_selection_scorecard`，选择原因写入 `candidate_selection_fallback_reason`。

这不是绕过安全检查。没有合格候选时，系统绝不会为了完成任务而强行选择；只有“模型偏好排序失败，但业务安全校验已经通过”时才会兜底。

## 工具任务快照

`ToolRegistry` 只对 Strategy 使用的纯计算和稳定证据工具建立任务级规范参数缓存。缓存键包含租户、工具名和 Pydantic 规范化后的参数；因此 `199` 与 `199.0` 会被视为同一次读取。第二次调用仍执行权限入口并生成 `ToolCallRecord`，但以 `recovered_result=true` 标记结果复用。

写工具、Seller Center 校验、销售指标、库存历史等可变读取，以及不同任务之间都不会共享该缓存，避免把写入前的旧状态或其他租户数据错误复用。缓存最多保留 2048 个任务、每个任务 128 个结果，并提供任务结束清理入口，避免长期服务内存无限增长。

## 验证

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v63
python -m pytest -q tests/test_v63_deterministic_finalization.py
python -m pytest -q
```

专项测试覆盖：选择输出截断、非法 ID 重选、四次硬预算、全部候选不合格和同参数工具结果复用。v64 将使用真实 DeepSeek 执行批量成功率、P95 Token、延迟和 429/5xx/超时故障注入验收。

本版全量离线回归结果：`557 passed`。
