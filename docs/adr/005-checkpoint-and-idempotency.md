# ADR 005: Checkpoint 与幂等共同恢复

## Context

浏览器超时后无法确定副作用是否发生，直接重跑可能重复发布或重复创建优惠券。

## Decision

Checkpoint 保存工作流状态，Resume 只重启必要节点；副作用工具使用持久化幂等键和
参数指纹，相同请求重放结果，不同参数复用同一键则拒绝。

## Alternatives

整条流程重跑更简单，但成本高且重复副作用风险大。仅 Checkpoint 不能解决外部写入歧义。

## Consequences

恢复范围小且副作用可控；代价是单机文件锁不适合多 Worker。

## Evidence

7 个 Recovery Case 和幂等 Browser Case 均通过，副作用恢复显示 replay 而非二次写入。

## Revisit Trigger

多实例部署时迁移到数据库事务、共享幂等表、租约和分布式锁。
