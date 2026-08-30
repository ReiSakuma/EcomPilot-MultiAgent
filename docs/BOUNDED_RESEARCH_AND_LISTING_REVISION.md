# 有限探索与文案返工机制

## 目标

本机制解决两个不同问题：

1. Market Agent 可能持续调用不同 SQL，直到耗尽 ReAct 步数，始终不提交市场报告。
2. Listing Agent 可能把竞品特征或推测信息写成当前商品功能，Review 发现后直接终止整条任务。

## Market 有限探索

Market Agent 仍由模型自主决定查询内容，但查询和总结使用不同预算：

1. 在运行时配置允许的情况下，最多执行两条成功的只读 SQL。
2. 每一步最多提交一条 SQL，SQL 仍经过 Schema、只读、租户过滤、行数和沙盒检查。
3. 达到查询预算后，ReAct Loop 添加强制总结指令，并把 `tool_choice` 设置为 `none`。
4. 最后一步只能根据已有证据输出 `MarketResearchModelOutput`，不能继续查询。
5. 若证据不足或与目标价格冲突，模型应明确报告不确定性，而不是继续寻找支持性证据。

完整 SQL 结果继续写入审计记录；返回模型的内容会截取必要字段和有限行数，控制上下文增长。

## Artifact 与 Review 受控返工

初始 DAG 仍然是：

`Market -> Listing/Strategy -> Review -> Browser`

当 Review 发现的阻断问题全部属于可通过文案修改解决的问题时，例如：

- `unsupported_product_claim`
- `prohibited_marketing_claim`

Review 返回结构化状态 `requires_revision`。每个阻断项还必须给出
`source_agent`、`artifact_type`、`field_path`、`claim_text` 和
`suggested_action`，编排器据此启动有界工作流循环：

`Review -> Listing.revise/Strategy.revise -> Review`

该循环具有以下约束：

1. 默认最多返工两次，计数保存在 `TaskState.workflow_loops.compliance_repair`。
2. Listing 与 Strategy 分别通过 `listing.revise`、`strategy.revise` A2A 能力执行；两边都有问题时可并行修订。
3. 修订输入同时引用 `ResearchEvidence` 和上一次 `RiskDecision` Artifact。
4. Strategy 修订只改 `launch_plan` 表达，保留已经由工具验证的价格、毛利和库存结果。
5. Agent 必须返回完整替换 Artifact，并记录实际应用的审核反馈。
6. Browser 在返工和复核期间被编排器门禁阻止，不能读取旧审核结果执行。
7. 相同审核项第二次出现时，循环启用确定性安全清理，不再为相同问题调用生成模型。
8. 每轮修订后必须再次经过独立 Review；两轮后仍违规则任务失败关闭。

价格、毛利、库存和执行安全等硬约束不会通过删除文字规避，仍然立即阻断任务。

## 停止条件与安全收尾

循环会为阻断项的错误码、来源 Agent、字段路径和违规原文生成 SHA-256 指纹。
同一指纹连续出现意味着模型修订没有解决问题，此时第二轮直接删除精确命中的未确认宣传。
安全收尾不会自动放宽毛利、库存或执行权限规则，也不会跳过最终 Review。

循环同时受到最大两轮返工、A2A 委派预算和工作流总步数限制，防止 Agent 无限互相返工。

## 可观测性

用户页面展示是否发生自动修订。运维页面展示：

- 当前返工阶段
- 已使用和最大返工次数
- 本轮实际返工的 Agent 列表和完成状态
- Review 与修订 Artifact 的引用
- 是否启用确定性安全收尾及最终停止原因
- Listing/Strategy 实际应用的审核项
- A2A `listing.revise`、`strategy.revise` 委派记录

这使严格 DAG 与有限 Agent Loop 保持明确区分，并为循环终止条件提供可审计证据。
