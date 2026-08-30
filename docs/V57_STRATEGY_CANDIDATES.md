# EcomPilot v57 Strategy 动态候选与工具裁决

## 本版目标

v57 把 Strategy Agent 从“一次同时想方案、算数字并作决定”重构为四个有边界的阶段：

1. `Evidence Selection`：模型最多自主选择两个可选证据工具。
2. `Candidate Proposal`：模型按商品类别、目标人群、市场和运营目标动态提出 2 至 4 个候选，不使用全品类固定优惠表。
3. `Candidate Evaluation`：本地确定性工具统一计算到手价、毛利、库存、促销预算和资格。
4. `Candidate Selection`：模型只能选择工具标记为 `eligible=true` 的候选 ID。

通俗地说，模型负责“想办法”，程序负责“验算和把关”。模型在文字里写错毛利率，不会改变最终可信数字。

## 协议与工具

`StrategyCandidateProposalOutput` 约束候选数为 2 至 4，候选 ID 必须唯一。促销继续使用 v56 的 `PromotionSpec 1.0`，因此 10 元券和 10% 优惠是不同结构，混合字段会被拒绝并最多修复一次。

`evaluate_strategy_candidates` 是只读、低风险的本地工具。它逐个返回到手价、毛利、库存、促销预算、`eligible` 和具体 `rejection_reasons`。赠品成本未知时仅该赠品候选得到 `gift_cost_unknown`，其他候选继续。

## 有限探索与强制收尾

- 可选证据工具最多 2 个。
- 候选 JSON 最多修复 1 次。
- 全部候选失败时，最多重新提案 1 次。
- 选择错误或已淘汰的 ID 时，最多重选 1 次。
- 重选仍失败时，从工具已验证候选中确定性收尾，不继续循环。
- 修复后仍无合格候选时返回 `requires_input` 和 `no_eligible_strategy_candidate`。
- 评估工具失败返回 `candidate_evaluation_tool_failed`，不伪装成业务拒绝。

预算、实际调用数、修复次数、候选提案、淘汰原因和最终候选 ID 都写入 Strategy Artifact、Trace 和运维 Strategy 页。

## 兼容边界

旧 checkpoint 和显式关闭候选管线的测试继续走 v56 单策略路径。真实运行中，只要 `strategy_agent` 启用 ReAct，`ECOMPILOT_STRATEGY_CANDIDATES=auto` 会默认开启 v57 管线；可设置为 `false` 紧急回退。

v57 不提前实现 v58 的完整确定性文案渲染与跨 Listing/Strategy/Review 修正审计。

## 验收

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v57
python scripts/run_v57_acceptance.py
python -m pytest -q tests/test_v57_strategy_candidates.py
python -m pytest -q
```

验收报告：`reports/v57/v57_acceptance.json`。
