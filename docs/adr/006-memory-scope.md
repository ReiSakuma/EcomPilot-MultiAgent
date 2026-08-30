# ADR 006: Memory 只作为 Context 辅助

## Context

自动长期记忆容易把模型错误、过期偏好和敏感数据持续传播。

## Decision

最终版不把 Memory 作为主亮点。只检索人工种子的品牌规则与品类经验，inactive 记录
不进入 Context，不自动写入 Reflection，也不引入向量数据库。

## Alternatives

完整 RAG、自动 Reflection Memory 或 GraphRAG 能支持更多知识，但需要来源、时效、删除、
冲突与评测体系，超出当前 MVP。

## Consequences

范围诚实、风险低；代价是不能宣称个性化自学习。

## Evidence

Interview Eval 验证 scoped memory、inactive 排除和 Context 压缩三种行为。

## Revisit Trigger

出现多商家长期偏好需求，并且具备人工确认、有效期、删除和离线评测后再扩展。
