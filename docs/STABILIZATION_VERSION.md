# EcomPilot v26 稳定化补丁

## 版本目标

本版本不增加 Agent、工具或动态 DAG。修改目标是统一模块协议、收敛错误传播，并让可选的高级能力不再轻易击穿商品上新主流程。

核心验收目标：真实模型生成任务必须在有限时间内结束；成功、业务拒绝、技术失败和可选能力降级必须能够被程序及页面明确区分。

## 保留的架构

- 固定主 DAG：Market -> Listing/Strategy -> Review -> Browser。
- Market 和 Strategy 内部的受限 ReAct 工具选择。
- Pydantic Artifact、A2A Handoff、能力令牌和状态版本检查。
- SQL AST 白名单、只读数据库、租户过滤和进程沙盒。
- Review 有限返工、人工审批、浏览器执行和执行后验证。

## 本次修改

### 1. 统一结果与失败协议

`TaskState` 新增：

- `outcome`：稳定的业务结果，不再要求页面根据底层节点猜测。
- `failure`：当前终止原因。
- `failure_history`：历史终止原因。
- `degradations`：未终止任务的可选能力降级记录。

任务结果包括：

- `awaiting_approval`：方案通过审核，等待用户确认。
- `completed`：已执行并验证。
- `business_rejected`：价格、库存或业务规则不满足。
- `technical_failed`：模型、工具、超时或内部协议失败。

所有失败统一为 `FailureEnvelope`，包含错误代码、错误分类、阶段、Agent、中文用户说明、开发者原始说明、是否可恢复、建议动作和 Trace 引用。

旧的 `status`、`error` 和 `agent_outputs` 继续保留，作为兼容层。

### 2. Market ReAct 收敛

- Market Agent 现在只允许选择一条最有价值的 SQL 查询。
- DeepSeek 同一轮返回多条 SQL 时，不执行任何一条，向模型反馈协议要求，并允许一次受控纠正。
- SQL 安全策略没有放宽；危险 SQL 仍然会被拒绝。
- 纠正后仍无法生成安全查询时，记录 `degradation`，使用基础市场样本继续主流程。
- 降级会在用户页和运维页明确展示，不会伪装成完整的 Text-to-SQL 成功。

### 3. ReAct 工具调用账本

工具结果除兼容的按工具名索引外，同时按 `call_id` 保存，并记录每个工具对应的调用 ID。相同工具多次调用时，历史结果不再只能依赖最后一次值。

### 4. 主流程减负

- Market SQL 属于增强证据，失败可以显式降级。
- Strategy ReAct 协议探索失败时，回到“受信计算工具 + 真实 LLM 表达”的核心路径。
- 模型服务认证、限流或不可用仍然会失败关闭，不会切换成规则模型冒充真实 LLM。
- Strategy 中把优惠金额写成百分比的文本会在进入 Review 前使用受信工具结果规范化；Review 仍保留第二道检查。

### 5. 接口和时间边界

- Agent 通过统一入口读取必需的上游输出，缺字段时产生 `workflow_contract_failed`，不再暴露普通 `KeyError`。
- 模型和工具适配器负责自身有限重试；整个节点不再默认重复执行，避免多层重试相乘。
- 新增任务级 `ECOMPILOT_WORKFLOW_TIMEOUT_SECONDS`，默认 120 秒，范围 30 到 600 秒。

### 6. 页面联动

API 响应新增稳定的 `presentation` 视图，用户页和运维页优先读取同一个 `outcome`、`failure` 和 `degradations`。

用户页打开运维后台时会携带 `task_id` 并固定该任务。只要 URL 已指定 `task_id`，后台轮询就不会切换到其他“最新任务”。

### 7. Strategy 内容修订协议

- Review 不再只依赖模型给出的错误名称决定是否返工。
- 当阻断项明确指向 Listing 或 Strategy 文本字段、包含具体问题片段，并要求删除、改写或修正优惠表达时，系统会归一化为受控内容修订协议。
- `execution_risk + rewrite_claim` 会转换为 `unsupported_product_claim`，退回对应 Agent 局部修订，然后重新执行 Review。
- Strategy 修订只替换 `launch_plan`，保留工具已经验证的价格、优惠、毛利、库存和策略证据，不重新执行整套 ReAct 探索。
- 最多修订两轮；重复问题触发不调用模型的安全收尾。真正的 `stop_execution`、毛利不足、库存不足、权限或执行风险仍然阻断。
- 安全促销模板不再假设存在“已确认卖点”，没有功能证据时使用中性的“品类定位”。

### 8. 运维端只读化

- `/ops` 只读取任务 Checkpoint、Trace 投影、运行时状态和模拟店铺快照。
- 删除新建任务、直接审批、审批后恢复、运行回归和重置店铺等写操作。
- 用户工作台成为生成方案、确认方案和同步店铺的唯一产品入口。
- 离线测试接口继续服务自动化验收，但不暴露为运维页面操作。

### 9. Strategy、Listing、Review 语义一致性与修正审计

本补丁在三个 Agent 之间增加了确定性的语义校验层。LLM 仍负责生成商品文案和策略表达，但价格、优惠、毛利、库存和已确认商品能力由程序中的受信事实约束，不能只凭模型文字决定。

- Listing 生成后检查卖点是否超出用户确认的功能。可安全修正的效果性承诺会改写成中性事实，例如将“蓝牙连接稳定、抗干扰性能好”收敛为“支持蓝牙 5.3”。
- Strategy 同时检查 `launch_plan` 和 `strategy_rationale`，确保优惠单位、到手价、毛利率和库存安排与受信工具结果一致。优惠 10 元不会再在说明文字中写成 10%。
- Review 在 LLM 审核之外执行第二遍跨模块检查，验证 Listing 是否仍包含无依据卖点，以及 Strategy 的文字、数字和工具结果是否一致。
- 可修正问题走有限返工或确定性校正，不直接终止任务；真实的毛利不足、库存不足、权限错误和执行风险仍会阻断。

每次自动修正都会生成结构化审计记录，包含：

- `correction_id`：本次修正的稳定标识；
- `source_agent` 和 `field_path`：问题来自哪个 Agent、哪个字段；
- `issue_code`：问题类型；
- `before` 和 `after`：修正前后的内容；
- `reason` 和 `evidence_refs`：修正原因及采用的证据；
- `method` 和 `status`：修正方法及处理状态。

修正记录会写入 Listing/Strategy Artifact，汇总到 Review 的 `correction_audit`，并以 `semantic_correction` 事件进入 Trace。用户页只显示“已安全校正 N 处”的业务提示；运维页可查看完整的修正前后内容和一致性检查结果。

## 关键与可选能力边界

关键能力失败会终止任务：

- DeepSeek 服务不可用或结构化输出最终无法校验；
- 核心价格、毛利和库存计算失败；
- Artifact、A2A、权限或状态版本协议不一致；
- Review 发现无法在预算内修复的业务问题；
- 浏览器执行或执行后验证失败。

可选能力失败会显式降级：

- Market Text-to-SQL 增强查询；
- Strategy ReAct 的证据探索协议。

降级不等于隐藏失败。任务响应和运维后台都会保存具体原因。

## 测试结果

- 完整自动化回归：275 项通过。
- 语义一致性专项测试覆盖优惠单位、策略数字、商品卖点收敛、Review 修正路由、Artifact 哈希和 Trace 审计事件。
- 用户页桌面端、移动端和业务阻断场景视觉检查全部通过，无控制台错误和横向溢出。
- 用户页、运维页、Trace 和模拟商家后台联动契约测试通过。
- 新增真实模型输出形态回放：同一轮两条 Market SQL -> 拒绝批次 -> 纠正为一条 -> 仅执行一条 -> 正常生成结果。
- 正常流程验证：`awaiting_approval`，店铺未修改。
- 业务失败验证：`business_rejected`，页面协议返回明确的毛利不足原因。

本版本没有执行付费的真实 DeepSeek 网络回归。真实 API 验证仍需使用用户本地 Key 启动联动服务后完成。
