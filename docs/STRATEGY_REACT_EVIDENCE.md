# Strategy ReAct 可选证据工具

Strategy Agent 采用“模型选择证据、程序验证底线”的受控自主模式。

## 可选证据

- `forecast_demand`：根据合成历史需求信号、价格弹性和季节系数生成有上下界的需求预测。
- `query_campaign_history`：查询相似人群和品类的合成历史活动、转化率与 ROI。
- `analyze_competitor_price_trends`：分析合成竞品价格时间序列及近期降价数量。
- `simulate_discount_scenarios`：用确定性公式比较候选优惠的毛利率、总毛利与库存约束。

模型根据当前目标自主决定是否调用这些工具，不要求全部调用。工具结果会写入 Strategy Artifact 的：

- `selected_evidence_tools`
- `decision_evidence`

所有演示证据都标记 `source_type=synthetic_seed` 或 `deterministic_simulation`，不得描述为实时平台数据。

## 不可跳过的硬校验

- `check_inventory`
- `simulate_discount_scenarios` 或 `calculate_margin` 中至少一个

优惠建议、优惠模拟和最终毛利计算共同调用 `maximum_safe_discount`，因此使用同一个首发优惠上限。模拟工具会同时返回毛利是否合格、是否符合优惠政策、可选优惠和被拒绝优惠；毛利为正但超过政策上限的候选仍标记为不可选。

模型即使提前给出答案，也会收到缺失证据提示并继续执行。模型不能修改用户输入的成本、售价、最低毛利率、库存、品类和目标人群。如果模型提交越界优惠，ReAct 会提供一次结构化纠错机会，要求它按统一上限重新计算；重复越界才会按照 `fail_closed` 终止任务。

## 运行配置

```bash
export ECOMPILOT_LLM_AGENTS=market_agent,listing_agent,strategy_agent,review_agent
export ECOMPILOT_REACT_AGENTS=market_agent,strategy_agent
export ECOMPILOT_LLM_FALLBACK=fail_closed
export ECOMPILOT_LLM_MAX_CALLS_PER_AGENT=7
export ECOMPILOT_STRATEGY_CANDIDATES=auto
export ECOMPILOT_REACT_MAX_STEPS=5
export ECOMPILOT_REACT_MAX_TOOL_CALLS=8
```

联动服务会拒绝缺少 Strategy ReAct 的配置，避免用户页面悄悄退回固定策略流程。

## 能力边界

当前需求、活动与竞品趋势数据都是面试演示用的合成数据，不是统计学习模型或实时商业预测。生产实现需要替换为数据仓库、活动平台和竞品数据服务，并增加数据新鲜度、质量评分与来源授权。
