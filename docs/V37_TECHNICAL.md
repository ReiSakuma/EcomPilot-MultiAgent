# EcomPilot MultiAgent V37 技术说明

## 版本目标

V37 在 V36 的可恢复执行基础上完成两项稳定增量：

1. 一条用户消息可以被编译为最多 5 个 `IntentUnit`，形成可审计的依赖 DAG。
2. 长会话上下文由确定性的 `ContextBudgetManager` 管理，摘要具有来源和版本，且不能单独授权写操作。

本版本号为 `0.37.0`，发布标识为 `v37-multi-intent-context`。

## 多意图请求编译

`RequestCompiler` 仍输出原有主意图，以兼容 V36 的单意图工作流；同时新增：

- `intent_units`：每个意图的文本、实体、读写模式、依赖、必填字段和能力范围。
- `conflicts`：同一轮中的字段冲突、模糊指代或超过 5 个意图等问题。
- `compiler_protocol_version=1.1`。

`RoutePlan 1.1` 把可执行单元分成若干 `execution_groups`：

- 没有依赖关系的只读意图进入同一并行组。
- 写计划始终串行，并且只能在依赖的只读证据完成后运行。
- 冲突请求不执行，先向用户澄清。
- 模型只能选择白名单工作流，不能创建任意节点或绕过权限。

`MultiIntentExecutor` 是有界 DAG 调度器。并行只读最多 4 路；依赖失败时，下游标记为 `blocked`；每个单元拿到的是前序结果副本和 Artifact 引用，不共享可变全局状态。

主会话图已接入并行只读组，并把各意图结果聚合为一个用户回答。研究后上架等读写组合按依赖顺序进入原有审批链；复杂的多个写任务不会在一轮中自动批量落店，而会被串行化或要求澄清。

## 上下文预算与压缩

`ContextBudgetManager` 不依赖模型判断是否压缩：

- 至少预留上下文窗口的 30% 给模型输出、工具结果和安全检查。
- 预计输入超过可用输入预算的 70% 时，启动分层压缩。
- 总占用超过窗口的 85% 时，要求拆分任务。
- P0 安全、租户和权限及 P1 当前硬约束始终保留。
- P2 Artifact 和实体关系保留为结构化数据。
- P3 旧会话可摘要。
- P4 调试噪声和重复轨迹直接丢弃。

## 摘要信任链

`StructuredConversationSummary 2.0` 新增来源消息、来源版本、Artifact 引用、事实快照、SHA-256 内容哈希和信任状态。

摘要只是可重建缓存，不是事实源。上下文组装前会重新检查消息版本、事实冲突和内容哈希。任一检查失败，摘要从模型上下文移除并记录 `context_event`。无论摘要是否有效，`summary_trust.write_authority` 永远为 `false`；成本、售价、库存等写入字段仍必须来自当前用户显式输入或经过验证的权威 Artifact。

## 可观测与证据

- 协议清单新增 `request_compiler 1.1`、`route_plan 1.1`、`conversation_summary 2.0` 和 `context_budget 1.0`。
- Run Bundle 升级为 `2.2`，增加上下文预算、摘要信任状态和上下文事件。
- `scripts/run_v37_context_acceptance.py` 验证依赖 DAG、并行只读、冲突澄清和分层压缩。
- `tests/test_v37_multi_intent_context.py` 验证摘要污染不会补齐写操作字段。

## 运行验证

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v37
python -m pytest -q
python scripts/run_v37_context_acceptance.py
python scripts/run_v37_reliability_acceptance.py
python scripts/run_v37_mvp_gate.py
```

真实 DeepSeek 和 Playwright 仍使用 V36 已有配置方式，离线验收不消耗 API 额度。

## 已知边界

- 多意图调度当前是单服务进程内的有界线程池，不是分布式任务队列。
- SQLite 适合面试演示和单实例验证，不代表每天数万请求的生产存储方案。
- 模拟商家后台不是真实电商平台。
- 上下文压缩目前是规则式结构化摘要，不宣称具备生产级语义无损压缩。
