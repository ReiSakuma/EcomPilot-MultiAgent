# EcomPilot Strategy 上下文、调用预算与截断稳定化技术路线

## 1. 文档目的

本文档用于指导 v59 之后对 Strategy Agent 和通用 ReAct Runtime 的稳定化改造，集中解决以下相互关联的问题：

1. 模型一次选择超过允许数量的证据工具，系统要求重选，导致额外模型调用。
2. 重试、纠错和工具结果不断追加到 ReAct 对话，使上下文越来越长。
3. 候选生成、候选修复和最终选择重复携带相同市场数据与完整 Artifact。
4. 模型输出因 `finish_reason=length` 被截断，JSON 不完整并触发二次修复。
5. 已经存在通过确定性工具检查的候选时，模型选择失败仍可能终止整个任务。
6. 全局 Context Manager 已存在，但没有覆盖 Agent 内部每一轮 ReAct 调用。
7. 增大 `max_tokens` 只能推迟问题，不能消除重复上下文和重试放大。

本路线不新增 Strategy 子 Agent。Strategy 仍是一个 Agent，但内部改为多个具有独立输入、输出和预算的阶段。

## 2. 根因总结

### 2.1 工具约束发生得太晚

当前模型可以在一个工具调用响应中选择三个证据工具，程序在收到完整响应后才检查“最多两个”。被拒绝后，系统把错误反馈追加到原对话，再让模型重选。

这虽然保证了安全，却带来：

- 一次无效模型调用；
- 一轮纠错消息；
- 一次重选调用；
- 更长的后续上下文；
- 更高的截断概率。

### 2.2 上下文只在 Agent 入场前整理

现有 `ContextManager` 会按优先级构造 Agent 初始上下文，会话摘要也能替代部分历史消息。但 ReAct 运行过程中形成的以下内容没有再次执行预算检查：

- 模型生成的工具调用消息；
- 工具完整结果；
- 工具批次拒绝反馈；
- 模型重新选择工具的结果；
- 强制结束提示；
- JSON 修复历史。

因此“初始上下文不大”不代表“最后一次模型调用的上下文不大”。

### 2.3 同一事实被重复包装

Strategy 候选提示中可能同时包含：

- 当前 `goal`；
- `trusted_constraints`；
- Market 关键字段；
- 完整 `ContextPackage.model_dump()`；
- `ContextPackage` 中再次出现的 task summary、selected parts 和 sections。

这会重复传递成本、售价、库存、目标人群和市场摘要。

### 2.4 输入预算和输出预算没有按阶段分开

证据规划、候选生成和候选选择使用不同复杂度的输出协议，但当前主要依赖统一模型配置。候选选择只需要返回一个 ID 和简短理由，不应拥有与长策略生成相同的提示和输出空间。

### 2.5 可恢复的模型失败被提升成任务失败

候选的毛利、库存和预算资格已经由确定性工具判断。此时模型只负责业务偏好排序。若选择调用截断，系统完全可以从合格候选中确定性收尾，不应让整个商品上新任务失败。

## 3. 改造原则

### 3.1 模型负责选择，框架负责限制

模型仍自主判断需要哪些证据，但选择必须先进入结构化 `EvidencePlan`。Pydantic 在工具执行前保证：

- 只能选择允许的工具；
- 去重后最多两个；
- 每个工具必须带选择原因；
- 不允许混入执行结果；
- `skip_reason` 解释为何不需要额外证据。

框架只执行校验后的计划，避免先调用三个工具再整批拒绝。

### 3.2 数字和资格由工具拥有

以下字段只允许来自确定性工具：

- 到手价；
- 优惠金额或优惠比例换算；
- 毛利额与毛利率；
- 计划投放数量；
- 库存剩余；
- 候选是否合格；
- 预算是否超限。

模型不能重新计算这些字段，也不能通过文字覆盖工具结论。

### 3.3 每个阶段只看到完成该阶段所需的信息

Strategy 内部分为：

```text
Evidence Planning
  -> Evidence Execution
  -> Candidate Proposal
  -> Deterministic Evaluation
  -> Candidate Selection
  -> Deterministic Rendering
```

不得用一段不断膨胀的消息历史贯穿所有阶段。

### 3.4 压缩不能破坏事实与权限

安全规则、租户、用户确认字段、最新 Checkpoint 版本和工具裁决不得由模型摘要。它们必须从结构化状态重新注入。

可以压缩或丢弃的内容包括：

- 模型自然语言解释；
- 已被拒绝的工具计划原文；
- 重复市场描述；
- 旧的候选长理由；
- Debug 日志；
- 已转换成结构化证据的原始工具文本。

### 3.5 有合格候选就必须有限收尾

只要存在至少一个通过工具检查的候选，模型排序失败、JSON 修复失败或输出截断都只能造成“选择能力降级”，不能造成业务任务失败。

## 4. 目标架构

### 4.1 StrategyStageContext

新增阶段专属上下文协议，不再向 Strategy 传递完整 `ContextPackage.model_dump()`：

```json
{
  "task_identity": {
    "task_id": "task_xxx",
    "checkpoint_version": 8
  },
  "trusted_constraints": {
    "category": "无线耳机",
    "price": 199,
    "cost": 95,
    "inventory": 800,
    "minimum_margin_rate": 0.25,
    "planned_units": 300
  },
  "confirmed_product_facts": {
    "features": ["蓝牙5.3", "游戏低延迟"],
    "product_form": "入耳式"
  },
  "market_digest": {
    "core_reference_price": 188.78,
    "accepted_price_band": [160.47, 217.10],
    "pain_points": ["连接稳定性", "续航真实性"]
  }
}
```

同一个事实只出现一次。

### 4.2 EvidencePlan

模型第一步只返回证据计划：

```json
{
  "selected_tools": [
    {
      "tool_name": "forecast_demand",
      "decision_question": "首批投放300件是否合理"
    }
  ],
  "skip_reason": null
}
```

协议约束：

- `selected_tools` 长度为 0 至 2；
- 工具名使用 Literal 白名单；
- 相同工具不能重复；
- Schema 不合格时只允许一次紧凑修复；
- 修复仍失败时，不调用可选证据工具，继续核心策略路径；
- 证据计划失败属于可选能力降级，不属于商品上新失败。

### 4.3 EvidenceLedger

工具执行结果转换为短小、可追溯的证据账本：

```json
{
  "forecast_demand": {
    "status": "completed",
    "decision": "planned_units_supported",
    "forecast_range": [260, 340],
    "confidence": "medium",
    "source_type": "synthetic_seed",
    "evidence_refs": ["tool_call_xxx"]
  }
}
```

原始工具结果仍保存在 Trace、Artifact 和 Run Bundle，但不重复进入后续模型上下文。

### 4.4 CandidateBrief 与 CandidateEvaluationBrief

候选生成阶段只读取 `StrategyStageContext + EvidenceLedger`，输出 2 至 4 个简洁候选。

确定性工具评估后，候选选择阶段只能看到：

```json
{
  "eligible_candidates": [
    {
      "candidate_id": "candidate_1",
      "objective": "首月冷启动",
      "promotion_type": "fixed_amount_coupon",
      "discount_amount_yuan": 10,
      "net_price": 189,
      "margin_rate": 0.4974,
      "planned_units": 300,
      "inventory_remaining": 500,
      "evidence_summary": ["需求范围支持首批300件"]
    }
  ]
}
```

禁止携带：完整市场报告、原始评论、全部 ReAct 消息、被拒绝候选的长文和工具原始响应。

### 4.5 BoundedContextController

在每一次模型调用前执行统一预算：

```text
模型上下文窗口
- 系统保留输出空间
- Schema 输出预算
- 安全余量
= 当前阶段最大输入预算
```

建议初始预算：

| 阶段 | 最大输入 | 最大输出 | 失败后处理 |
| --- | ---: | ---: | --- |
| 语义编译 | 2500 | 1800 | 一次紧凑重试 |
| 证据规划 | 1200 | 500 | 无证据降级 |
| 候选生成 | 3000 | 1800 | 一次紧凑 JSON 修复 |
| 候选修复 | 1800 | 1400 | 仍无合格候选则请求用户调整 |
| 候选选择 | 1200 | 500 | 确定性强制选择 |
| Listing | 3000 | 1800 | 紧凑重试或受控安全文案 |
| Review | 2200 | 1200 | 紧凑重试或确定性一致性检查 |

以上数字是项目初始配置，需要通过真实 DeepSeek 测试校准，不能写死在 Prompt 中。

## 5. ReAct 循环压缩策略

### 5.1 每一步调用前检查

`BoundedReactLoop` 在调用模型前记录：

- 当前输入 Token 估算；
- 为输出预留的 Token；
- 已调用工具数量；
- 已发生纠错次数；
- 剩余步骤数；
- 是否需要压缩或强制收尾。

### 5.2 不直接删除半个工具调用协议

OpenAI 兼容的 Tool Calling 要求 assistant tool call 与 tool result 成对存在。不能在原消息数组中随意删除其中一半。

需要压缩时应启动新的合法模型会话：

```text
System Prompt
+ 当前可信约束
+ EvidenceLedger
+ 已拒绝动作摘要
+ 当前必须完成的输出 Schema
```

旧对话完整保存在 Trace，但不再发送给模型。

### 5.3 压缩触发条件

满足任一条件即触发：

1. 预计输入超过阶段预算的 70%；
2. 已发生一次工具批次拒绝；
3. 已执行两个可选证据工具；
4. 即将进行最终候选选择；
5. 上一轮出现 `finish_reason=length`；
6. 同一事实在上下文中出现两次以上。

### 5.4 压缩优先级

必须保留：

1. 安全规则、租户和权限；
2. 当前任务与 Checkpoint 版本；
3. 用户明确确认的成本、售价、库存、最低毛利率和商品事实；
4. 最新工具裁决；
5. 当前可选候选 ID；
6. 必须满足的输出 Schema。

优先丢弃：

1. 旧模型解释和思维过程；
2. 被拒绝工具调用的完整参数；
3. 重复 Schema；
4. 市场原始样本；
5. 已经结构化的工具长结果；
6. Debug 和 UI 展示字段。

## 6. 调用与重试预算

### 6.1 避免多层重试相乘

必须明确每层唯一责任：

- HTTP 瞬时错误：ModelAdapter 最多重试 2 次，指数退避并加入随机抖动。
- JSON/Schema 错误：调用方最多修复 1 次。
- 工具参数错误：ReAct Runtime 最多纠正 1 次。
- 工具业务拒绝：不重试同一参数，直接换候选或结束探索。
- 候选选择错误：最多紧凑重选 1 次，随后确定性收尾。
- Agent 节点：不因下层已经重试过而整体重新运行。
- Workflow：只从 Checkpoint 恢复未完成阶段，不从 Market 重新开始。

### 6.2 Strategy 总预算

建议一个正常 Strategy 请求的模型调用预算为：

```text
证据规划 1 次
+ 候选生成 1 次
+ 最终选择 1 次
= 正常 3 次
```

异常情况下最多增加：

```text
Schema 修复或候选修复 1 次
```

因此 Strategy 正常为 2 至 3 次，硬上限建议为 4 次。达到上限后必须进入确定性收尾，不允许继续要求模型重选。

工具调用预算：

- 可选外部证据工具：0 至 2 个；
- 候选批量评估工具：固定 1 次；
- 不允许对每个候选分别调用三套相同计算工具；
- 同一工具同一参数在一个任务内使用结果缓存；
- 相同 `tool_name + normalized_args + evidence_version` 命中缓存时不重复执行。

## 7. 输出截断处理

### 7.1 分阶段输出上限

为 `complete_with_tools()` 增加按调用传入的 `max_output_tokens`，不得只依赖全局默认值。候选选择使用较小而足够的输出预算，候选生成使用更大的预算。

### 7.2 `finish_reason=length` 分类处理

| 发生阶段 | 处理方式 |
| --- | --- |
| 证据规划 | 使用精简 Schema 重试一次；仍失败则跳过可选证据 |
| 候选生成 | 使用压缩上下文重试一次；仍失败则执行 verified core strategy |
| 候选修复 | 不再重试，返回没有合格候选的业务说明 |
| 候选选择 | 不重试长上下文；直接从合格候选确定性选择 |
| Listing | 精简上下文与输出字段重试一次 |
| Review | 使用最多五条 finding 的紧凑协议重试一次 |

### 7.3 确定性强制选择

模型最终选择失败时，框架从合格候选中按照配置化评分选出一个：

```text
score =
  经营目标匹配分
  + 证据支持分
  + 预计总毛利归一化分
  - 优惠成本惩罚
  - 证据不确定性惩罚
```

评分不得重新判定候选资格。资格仍然完全属于确定性工具。

页面显示：

```text
策略候选已通过业务校验；模型排序未完成，系统按已验证经营指标完成选择。
```

Trace 记录 `strategy_selection_degraded`，但任务继续进入 Review。

## 8. 可观测性与页面

每次模型调用新增以下记录：

- `stage`；
- `input_token_estimate`；
- `actual_input_tokens`；
- `reserved_output_tokens`；
- `actual_output_tokens`；
- `context_compressed`；
- `compression_reason`；
- `dropped_sections`；
- `retry_index`；
- `finish_reason`；
- `forced_finalize`；
- `fallback_action`。

运维端 Strategy 页面展示：

```text
证据工具：1/2
模型调用：3/4
上下文压缩：1次
候选：生成3个，通过2个
最终选择：模型选择 / 确定性收尾
截断：0次
```

用户端不展示 Token 和底层错误，只展示最终业务状态和是否发生安全降级。

## 9. 增量版本规划

### v60：基线与上下文去重

修改：

1. 为全部模型调用记录阶段级输入、输出和 `finish_reason`。
2. 新增 `StrategyStageContext`，停止传递完整 `ContextPackage.model_dump()`。
3. 去除 `goal`、约束和市场数据的重复字段。
4. 候选选择只接收紧凑的合格候选投影。

测试：

- 相同任务的 Strategy 初始输入 Token 至少下降 30%。
- 候选选择 Prompt 不包含原始市场样本和完整 Artifact。
- 现有业务结果、数字和审核结果不变。

### v61：结构化证据规划

修改：

1. 新增 `EvidencePlan` Pydantic 协议，长度限制为 0 至 2。
2. 将“模型自由发起多个工具调用”改为“模型先规划，框架后执行”。
3. 新增 `EvidenceLedger` 和工具结果短投影。
4. 证据规划失败时显式降级，不终止商品上新。

测试：

- 模型返回三个工具时，第三个在执行前被 Schema 拒绝或受控修复。
- 不产生三工具整批执行或整批拒绝记录。
- 零工具、一个工具和两个工具均可正常完成。

### v62：逐调用预算与 ReAct 滚动压缩

修改：

1. 新增 `BoundedContextController`。
2. 每轮 ReAct 调用前执行 Token 预算检查。
3. 触发压缩时创建新的合法 Tool Conversation，并注入 EvidenceLedger。
4. 为不同阶段设置独立输入和输出预算。

测试：

- 长工具结果不会原样进入下一轮模型调用。
- 一次工具拒绝后，下一轮上下文长度不增加或明显下降。
- 压缩后 Tool Calling 消息协议仍合法。
- P0/P1 可信字段在压缩后保持完全一致。

### v63：候选强制收尾与重试收敛

修改：

1. 候选选择出现截断、异常或非法 ID 时确定性收尾。
2. 统一 ModelAdapter、Schema、ReAct 和 Workflow 的重试所有权。
3. Strategy 模型调用硬上限设为 4。
4. 增加工具幂等缓存和相同参数去重。
5. 只有“所有候选均不合格”才进入用户补充或业务拒绝。

测试：

- 候选选择抛出 `ModelIncompleteError`，任务仍进入 Review。
- 非法候选 ID 重选一次后强制结束。
- 全部候选不合格时不错误选择。
- Strategy 调用次数永远不超过配置硬上限。

### v64：真实 DeepSeek 压测与最终验收

修改：

1. 运维端增加上下文、预算、压缩和降级指标。
2. Run Bundle 增加 Context Budget 与 Evidence Plan 证据。
3. 使用真实 DeepSeek 执行稳定性测试并校准阈值。
4. 固化面试演示案例和故障注入报告。

测试矩阵：

- 正常上新 20 次；
- 模型首次选择三个工具 10 次；
- 工具返回超长结果 10 次；
- 候选生成 JSON 截断 10 次；
- 候选选择截断 10 次；
- DeepSeek 429/5xx/超时故障注入；
- 长会话、恢复任务和多任务切换；
- 无合格候选和单一合格候选。

验收指标：

- Strategy 工具超限导致的任务失败数为 0。
- 已有合格候选时，选择阶段导致的任务失败数为 0。
- Strategy 单任务模型调用 P95 不超过 4 次。
- Strategy 上下文输入 Token P95 相比 v59 下降至少 35%。
- 输出截断后的可恢复率为 100%。
- 正常任务端到端成功率不低于 95%。
- 所有降级均可在 Trace 和 Run Bundle 中解释。

#### v64 实施结果

已增加统一稳定性统计、`smoke/full` 真实 DeepSeek 套件、确定性故障注入、最终目标覆盖矩阵、运维稳定性面板与 Run Bundle 四项新证据。真实 DeepSeek 是否通过由 `reports/v64/live_deepseek_*.json` 单独声明；未配置 Key 时只允许得到 `external_blocked`，不得以离线回归代替。

## 10. 不应采用的方案

1. **只提高 `max_output_tokens`**：会增加成本和延迟，不能解决重复上下文。
2. **无限重试模型**：容易造成调用爆炸，并可能重复生成同一种错误。
3. **删除工具数量上限**：模型可能机械调用全部工具，成本和延迟不可控。
4. **让模型总结安全字段**：摘要错误可能污染售价、库存、毛利和权限。
5. **把所有候选写死在程序里**：会失去按品类和市场情况制定策略的价值。
6. **任意删除 Tool Calling 历史消息**：可能破坏 tool call 与 tool result 的协议配对。
7. **候选选择模型失败就结束任务**：模型偏好排序不是业务安全门禁。

## 11. 最终预期流程

```text
可信结构化任务状态
  -> 阶段上下文投影与预算检查
  -> 模型生成 EvidencePlan（0-2个工具）
  -> 框架执行工具并生成 EvidenceLedger
  -> 模型生成 2-4 个业务候选
  -> 工具批量计算并过滤候选
  -> 模型在紧凑上下文中选择
      -> 成功：采用模型选择
      -> 截断/非法/超时：确定性强制收尾
  -> 程序渲染受信数字
  -> Review
  -> 用户确认与店铺同步
```

该路线保留了 Agent 的自主证据选择和策略创意，同时把工具数量、Token、重试、业务数字和终止条件交给框架治理。最终目标不是让模型永不出错，而是让任何一次可恢复的模型错误都无法放大成无限调用、上下文膨胀或整个任务失败。

## 12. v60 实际交付记录

版本：`0.60.0`

完成内容：

1. 新增 `StrategyStageContext`，将可信约束、已确认商品事实和市场摘要投影为阶段专属协议。
2. Strategy 候选生成、固定策略路径和 ReAct 路径停止接收完整 `ContextPackage.model_dump()`。
3. 候选选择阶段只接收合格候选短投影，剔除原始市场样本、调试负载和无关 Artifact。
4. 新增统一模型遥测协议，成功和失败调用均记录阶段、输入估算、实际输入、输出预算、实际输出和结束原因。
5. 保持 v59 历史发布证据接口兼容，项目版本和离线门禁升级到 `0.60.0`。

量化结果：

- 压力测试夹具中，Strategy 上下文估算由 2809 Token 降至 161 Token，下降 94.27%。
- 该结果证明去重机制生效，但不能替代 v64 的真实 DeepSeek P95 验收。
- 全量回归 `546 passed`，无失败。

进入下一版结论：v60 的上下文协议和遥测基线已经完成，可以进入 v61 的结构化 `EvidencePlan` 与 `EvidenceLedger` 改造。

## 13. v61 实际交付记录

版本：`0.61.0`

完成内容：

1. 新增受 Pydantic 约束的 `EvidencePlan`，工具选择在执行前被限制为 0 至 2 个且必须唯一。
2. 将候选策略流程拆为“模型规划、框架执行、Ledger 投影、模型生成”，候选生成阶段不再开放工具调用。
3. 工具参数统一由框架从可信任务状态映射，模型不能自行修改售价、品类、受众或查询边界。
4. 新增 `EvidenceLedger`，只向候选生成注入决策、有限事实、置信度和引用；原始结果保留用于 Trace 与审计。
5. 计划修复失败或单个可选工具失败时显式降级并继续主流程，不再把可选证据问题升级为整单失败。
6. `StrategyArtifact` 和 Run Bundle 增加 EvidencePlan / EvidenceLedger 证据与治理检查。

专项验证：

- 三工具与重复工具计划会在执行前被拒绝或受控修复。
- 零工具、一个工具、两个工具和单工具失败场景均可完成候选生成。
- 工具异常原文不会进入候选生成 Prompt。
- 旧版 Listing / Review 循环与 A2A Artifact 契约保持兼容。
- 专项与跨模块定向回归 `39 passed`，全量离线回归 `550 passed`。

进入下一版结论：v61 已解决“Strategy 一次选择超过上限工具后反复重选”的结构性来源，可以进入 v62 的逐调用预算与 ReAct 滚动压缩。

## 14. v62 实际交付记录

版本：`0.62.0`

完成内容：

1. 新增 `BoundedContextController`，每轮 ReAct 调用前计算消息和工具 Schema 的输入预算。
2. 超过软阈值后，框架重建合法 Tool Conversation，以有限 Rolling Evidence Ledger 替换旧工具历史。
3. system/user P0/P1 输入逐字保留，并记录 SHA-256 供 Trace 和 Run Bundle 核验。
4. 超长成功结果与工具拒绝反馈均会受控投影，避免重试后上下文继续增长。
5. Tool Calling Provider 支持逐调用 `max_output_tokens`；Market、Strategy、Analytics 采用独立阶段预算。
6. Market、Strategy、Analytics Artifact 正式接入上下文预算摘要，保持 A2A Schema 对齐。
7. Run Bundle 增加 `react_context_budget` 验证项。

验证结果：专项与跨模块扩展回归 `103 passed`，全量离线回归 `554 passed`。

进入下一版结论：v62 已控制 ReAct 每轮输入增长，可以进入 v63 的候选确定性强制收尾与重试所有权收敛。

## 15. v63 实际交付记录

版本：`0.63.0`

完成内容：

1. 新增 Strategy 四次逻辑模型调用硬预算，所有候选阶段和修复动作共享同一账本。
2. 明确传输重试、JSON 修复、候选修复、候选重选和 Workflow 的唯一所有者。
3. 候选选择截断、异常、非法 ID 或预算耗尽时，从工具验证通过的候选中确定性强制收尾。
4. 唯一合格候选跳过模型选择；只有全部候选不合格时才要求用户调整业务条件。
5. 候选模型或评估工具发生技术故障时，降级到工具验证的核心策略并继续 Review。
6. 安全只读工具增加任务级规范参数结果缓存，重复读取保留审计但不重复访问底层能力。
7. Strategy Artifact 增加选择模式、降级原因、确定性分数表和逻辑调用账本。

专项验证覆盖选择截断、非法 ID、两类结构修复共同耗尽预算、工具缓存和无合格候选；全量离线回归 `557 passed`。进入下一版结论：v63 已完成代码层稳定收尾，可以进入 v64 的真实 DeepSeek 批量压测与最终验收。
