# V62 ReAct 滚动上下文与逐调用预算

## 1. 版本目标

v62 解决 ReAct 多轮运行时的上下文增长问题。旧流程每调用一次工具，就把 assistant tool call 和完整 tool result 永久追加到下一轮请求；工具结果或拒绝反馈较长时，后续每一轮都会重复携带这些内容，最终导致输入过长、输出空间不足和 JSON 截断。

本版目标不是简单删除历史，也不是让模型再生成一份可能出错的摘要，而是用框架可验证的结构化压缩替换已经完成的工具过程。

## 2. 每轮处理流程

```text
合法 ToolConversation
  -> 估算 messages + tool schemas 的输入 Token
  -> 未超过软阈值：原样调用模型
  -> 超过软阈值：
       保留原始 system prompt
       保留原始 user prompt
       投影已完成/拒绝工具为 Rolling Evidence Ledger
       保留有限的框架控制指令
       新建合法 ToolConversation
  -> 使用该阶段独立 max_output_tokens 调用模型
```

`ToolConversation` 会重新检查工具调用和结果是否配对。压缩后的消息通常是 `system -> user -> user framework ledger`，不存在被截断的 assistant/tool 半对话。

## 3. BoundedContextController

控制器在每次 ReAct 模型调用前工作，输入包括：

- 当前合法消息；
- 原始 system/user prompt；
- 当前 Agent 可用工具 Schema；
- 已完成或被拒绝工具的滚动账本；
- 本阶段输入预算和输出预留。

输出 `ReactContextDecision`，记录压缩前后 Token、阈值、保留证据数量、丢弃旧证据数量和两个受保护 Prompt 的 SHA-256。

## 4. 可信信息保护

P0/P1 信息采用“原文保留”，不采用模型摘要：

- system prompt 的职责、权限和输出协议不改写；
- user prompt 中的成本、售价、库存、毛利和问题不改写；
- 工具结果只作为证据，不允许覆盖受保护事实；
- 压缩前后用 SHA-256 证明两段受保护内容完全相同。

如果 system/user 与工具定义本身就超过硬输入额度，控制器不会擅自截断，而是在模型调用前返回受控的 `ReactLoopLimitError`，交给 Agent 既有降级策略处理。

## 5. 滚动证据账本

账本记录工具名、调用 ID、状态和有限结果投影。它同时覆盖：

- `completed`：工具已执行并返回；
- `rejected`：参数或批次被规则拒绝，附有限纠正说明；
- `failed`：工具执行失败的受控描述。

单项结果和账本条数均有限制。原始长结果仍保存在 Tool Record 和 Trace 中用于审计，但不会原样回灌模型。控制器不会把上一份 framework ledger 再嵌套进下一份 ledger。

## 6. 分阶段预算

全局默认配置：

- ReAct 输入硬预算：12000 Token；
- 软压缩阈值：输入预算的 70%；
- Tool Calling 输出预留：1600 Token。

Agent 还会收紧到阶段上限：Market 10000/1200、Strategy 9000/1600、Analytics 9000/1400。前一个数字是输入上限，后一个数字是输出上限。可通过环境变量调整全局上限，但不能突破代码设置的阶段边界。

## 7. 可观测与审计

- `ReactLoopResult` 保存每轮 `context_decisions` 和 `compression_count`；
- Market、Strategy、Analytics Artifact 保存上下文预算摘要；
- 压缩发生时 Trace 写入 `react_context_budget` 事件；
- Run Bundle 检查压缩后 Token 不超过输入预算，并确认受保护 Prompt 哈希存在。

## 8. 测试覆盖

专项测试验证：

1. 12000 字符工具结果不会原样进入下一轮；
2. 超长工具拒绝反馈在重试前被压缩；
3. 压缩后消息仍能通过 `ToolConversation` 协议校验；
4. system/user 内容逐字保持；
5. 受保护输入自身溢出时模型不会被调用；
6. 真实 ModelAdapter 将逐调用输出额度写入 DeepSeek `max_tokens`。

运行：

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v62
python -m pytest -q tests/test_v62_react_context_budget.py \
  tests/test_v19_react_loop.py \
  tests/test_v21_text_to_sql.py \
  tests/test_strategy_evidence_react.py
python -m pytest -q
```

## 9. 当前边界

v62 负责让每一次 ReAct 调用的输入可控，但尚未改变候选选择模型失败时的最终裁决方式。候选 JSON 截断、非法候选 ID、模型超时后的确定性强制收尾属于 v63；真实 DeepSeek 的成功率、Token P95 和故障注入属于 v64。

## 10. 验收结果

- v62 专项与跨模块扩展回归：`103 passed`；
- 全量离线回归：`554 passed`；
- Python 编译检查通过；
- 本轮未调用真实 DeepSeek，未消耗 API 额度。
