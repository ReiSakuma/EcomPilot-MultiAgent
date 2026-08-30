# V60 Strategy 上下文与调用基线

## 1. 版本目标

v60 是《Strategy 上下文、调用预算与截断稳定化技术路线》的第一轮实现。它不改变商品上新的业务顺序，也不新增 Agent，而是先解决两个基础问题：Strategy 每个阶段究竟需要看什么，以及一次模型调用究竟消耗了多少输入和输出预算。

本版目标是让后续的工具上限前置、ReAct 循环压缩和截断降级建立在可测量、可回归的基础上。

## 2. 本版完成内容

### 2.1 StrategyStageContext

新增 `app/context/strategy.py`，将 Strategy 输入投影为五组结构化信息：

1. `task_identity`：任务 ID 与 Checkpoint 版本。
2. `trusted_constraints`：类别、成本、售价、库存、最低毛利率、计划投放量和运营目标。
3. `confirmed_product_facts`：用户确认的功能和商品形态。
4. `market_digest`：核心参考价、可接受价格带、样本量、市场卖点和用户痛点。
5. `protocol_version`：阶段协议版本，便于后续兼容和审计。

Strategy 仍会调用全局 Context Manager 来保留现有的上下文预算、压缩记录和 Trace 指标，但真正发给候选生成、候选选择和 ReAct 推理的内容改为 `StrategyStageContext`。因此历史会话、原始评论、商品 ID 列表、市场清洗决策和完整 Artifact 不再重复进入 Strategy Prompt。

### 2.2 候选选择输入最小化

候选选择器只看到通过确定性工具校验的候选摘要：

- 候选 ID 与经营目标；
- 促销类型与优惠金额；
- 工具计算的到手价、毛利额、毛利率；
- 计划投放量、剩余库存；
- 简短证据引用。

模型看不到原始市场样本、调试字段和被拒绝候选的长文本。数字结论仍归确定性工具所有，模型只负责在合格候选中表达业务偏好。

### 2.3 统一模型调用遥测

新增 `app/model/telemetry.py`，并让 Base Agent、Market、Strategy、Analytics、语义编译器和通用问答使用同一记录协议。每条模型记录至少包含：

| 字段 | 含义 |
| --- | --- |
| `stage` | 这次调用属于哪个步骤 |
| `input_token_estimate` | 发请求前估算的输入 Token |
| `actual_input_tokens` | API 返回的实际输入 Token |
| `reserved_output_tokens` | 本步骤允许的最大输出 Token |
| `actual_output_tokens` | API 实际生成的输出 Token |
| `finish_reason` | 正常结束、长度截断或其他结束原因 |

失败记录也使用相同字段，因此以后能区分“输入太长”“输出额度不足”“结构化校验失败”和“外部 API 不可用”。

## 3. 数据流变化

修改前：

```text
完整 ContextPackage
  + goal
  + constraints
  + market output
  + ContextPackage 中重复的上述内容
  -> Strategy 各阶段
```

修改后：

```text
TaskState / Market Artifact
  -> 确定性字段投影
  -> StrategyStageContext
  -> Candidate Proposal / ReAct
  -> Deterministic Evaluation
  -> Compact Eligible Candidate Brief
  -> Candidate Selection
```

## 4. 验收标准

自动化测试覆盖以下条件：

1. Strategy 阶段输入相对旧完整 `ContextPackage` 至少减少 30%。
2. 阶段输入不包含 `recent_turns`、市场 `decisions` 和 `product_ids`。
3. 核心成本、售价、市场参考价和运营目标仍被保留。
4. 候选选择 Prompt 不包含原始市场样本和 Debug 负载。
5. 成功与失败模型记录都包含阶段、预算、用量和结束原因。
6. 原有候选生成、工具校验、Strategy ReAct 与 Review 回归保持通过。

在包含超长会话历史、100 个商品 ID 和 100 条市场清洗决策的压力样例中，旧 `ContextPackage` 估算为 2809 Token，v60 的 `StrategyStageContext` 估算为 161 Token，下降 94.27%。这个数字是测试夹具的定点结果，不代表真实流量 P95；真实 P95 将在 v64 使用 DeepSeek 调用样本校准。

执行：

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v60
python -m pytest -q tests/test_v60_strategy_context.py \
  tests/test_v57_strategy_candidates.py \
  tests/test_strategy_evidence_react.py \
  tests/test_review_optimization.py
python -m pytest -q
```

## 5. 本版边界

v60 还没有实现以下后续路线内容：

- 独立的 `EvidencePlan`，在执行前强制可选证据工具为 0 至 2 个；
- 每一轮 ReAct 调用前的 `BoundedContextController`；
- 工具原始结果转换成短 `EvidenceLedger`；
- `finish_reason=length` 时从合格候选中确定性收尾；
- 跨阶段 P95 Token、调用次数和截断率仪表盘。

这些能力需要依次建立在 v60 的阶段协议和统一遥测之上，避免再次通过零散补丁处理超限与截断。

## 6. 验收结果

- 新增 v60 专项测试：3 项通过。
- Strategy、候选、ReAct 与 Review 定向回归：30 项通过。
- 全项目回归：546 项通过，0 项失败。
- Python 编译检查通过。
- v60 目录约 30 MB；未复制 v59 的大型运行时数据库、会话、Trace 和浏览器产物。

## 7. 面试讲解重点

v60 的价值不在于简单地“把 Prompt 写短”，而在于建立了 Agent 阶段输入协议：共享状态仍然完整保存用于审计和恢复，每个 Agent 阶段只读取完成职责所需的最小投影。这样既减少 Token，又降低旧历史、原始数据和无关字段干扰模型决策的概率，同时保留可追溯证据。
