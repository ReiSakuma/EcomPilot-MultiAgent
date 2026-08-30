# ADR 001: Multi-Agent 与固定工作流结合

## Context

新品上架同时包含证据读取、语言生成、确定性计算、合规审核和高风险写入。

## Decision

使用固定拓扑和五个职责 Agent。Agent 的成立依据是 Context、工具权限和失败边界；
Listing 与 Strategy 在 Market 后可并行，Review 汇合二者。

## Alternatives

单 Agent 更短，但权限与失败归因混在一起。纯规则 Workflow 更稳定，但语言生成能力弱。

## Consequences

获得隔离、可观测和局部恢复；代价是 Handoff 合同和状态管理更复杂。

## Evidence

40 Case Eval 覆盖各层失败，未授权副作用为 0；Recovery 能只重启受影响分支。

## Revisit Trigger

如果任务始终只有一个生成步骤且不含不同权限，合并为单 Agent。
