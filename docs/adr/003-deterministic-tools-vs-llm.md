# ADR 003: 硬约束使用确定性工具

## Context

毛利、库存、违规词和审批既可以问 LLM，也可以用确定性逻辑判断。

## Decision

LLM 只生成策略语言和 Review notes；毛利、库存、合规词与审批由工具和代码决定。

## Alternatives

LLM-only 实现更短，但同一输入可能产生不同结论，且难以证明边界完整。

## Consequences

获得可重复、可测试的业务安全边界；代价是新增规则需要工程维护。

## Evidence

Review 消融中，确定性规则违规漏过率为 0；宽松 LLM-only fixture 漏过三类违规。

## Revisit Trigger

软性规则可由模型辅助评分，但任何资金、库存或权限硬约束仍需确定性最终裁决。
