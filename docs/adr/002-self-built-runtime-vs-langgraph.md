# ADR 002: 自研业务 Runtime（后续被混合运行时部分替代）

> 状态：Superseded in v28+。会话和任务 Checkpoint 已采用 LangGraph；类型化业务状态、
> 工具网关、幂等和 Trace 仍由项目实现。当前结构见 `docs/ARCHITECTURE.md`。

## Context

系统已实现 State、Node、依赖、Checkpoint、Resume 和 Trace，是否迁移 LangGraph。

## Decision

面试最终版保留自研 Runtime，不做框架迁移。

## Alternatives

LangGraph 提供成熟图执行和持久化生态；Microsoft Agent Framework 提供另一套 Agent
抽象。两者都可行，但迁移不能直接补足当前最缺的真实指标和故障证据。

## Consequences

代码路径透明、依赖少；代价是动态图、分布式执行和框架生态能力较弱。

## Evidence

82 个测试、40 Case Eval 和 7 个 Recovery Case 能覆盖当前运行时合同。

## Revisit Trigger

需要动态图、跨进程持久化、人工节点编排或团队标准化框架时重新评估。
