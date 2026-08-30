# EcomPilot MultiAgent v36 技术说明

## 1. 版本目标

v36 在 v35 面试 MVP 上完成“异常收敛与可恢复执行”。目标不是让失败永远消失，而是让超时、限流、返回格式错误、并发冲突和进程异常都进入同一套可解释、有限重试、可恢复的处理流程，并且禁止不确定写操作被盲目重复执行。

本版不包含 v37 的多意图拆分与上下文压缩，也不宣称已经具备 v38 的多实例分布式锁、Fencing Token 和共享熔断状态。

## 2. 新增模块

### 2.1 统一故障分类

`app/reliability/classifier.py` 将异常归到八类：

| 分类 | 通俗解释 | 默认动作 |
|---|---|---|
| `transient` | 网络抖动、超时、502 等暂时故障 | 指数式短退避，最多三次尝试 |
| `rate_limit` | 429，服务端要求降低请求速度 | 优先遵守 `Retry-After` |
| `schema_invalid` | JSON 或字段结构不符合协议 | 受控修复后再生成，仍需 Schema 校验 |
| `business_rule` | 毛利、库存等业务条件不通过 | 不重试，要求调整业务输入 |
| `permission_denied` | 身份、租户或能力权限不足 | 不重试，失败关闭 |
| `concurrency_conflict` | 数据版本过旧或并发修改冲突 | 重新读取后有限重试 |
| `permanent` | 资源不存在或能力不支持 | 不重试 |
| `unknown` | 无法可靠分类的异常 | 保守尝试一次，随后转人工关注 |

错误签名由 `Agent + Tool + 错误码 + 规范化错误消息` 计算。任务 ID、时间戳和长数字会被去除，因此相同根因不会因为每次参数不同而被误认为新错误。

### 2.2 任务级重试预算

过去 Model、Tool 和 Node 各自重试，容易形成嵌套放大。v36 的 `RetryBudget` 随 `TaskState` 持久化，所有工具和整个节点的再次尝试共同消耗一份额度。默认总额度为 8，单个组件还受自己的最大尝试次数约束。

运维页 Reliability 标签会显示：已消耗额度、剩余额度、每次决策原因和错误签名。

### 2.3 ToolSpec 2.0

每个工具现在公开完整生命周期声明：

- `operation_type`：只读还是写入。
- `idempotency`：重复调用是否安全，或是否依赖幂等键。
- `timeout_seconds`：单次调用截止时间。
- `retryable_errors`：允许重试的故障类别。
- `compensation`：写错后的补偿方式。
- `reconcile_tool`：写操作结果不明时使用哪个工具回查。
- `tenant_scoped`：是否按租户隔离。
- `concurrency_limit`：声明的并发上限。
- `circuit_failure_threshold`：连续同类故障多少次后熔断。

`browser_execute` 是本版唯一写工具，声明为 `keyed` 幂等、单并发，并由 `browser_verify` 做权威回查。

### 2.4 熔断、死信与人工关注

同一租户的同一依赖连续出现三次相同临时故障时，`CircuitBreakerRegistry` 打开熔断器，短时间内拒绝继续冲击故障依赖。无法安全自动恢复的任务进入 `needs_attention`，并写入 SQLite Dead Letter Queue（死信队列）。

死信按租户过滤，相同任务和错误签名只保留一条待处理记录。运维台只读展示，不提供绕过用户动作边界的执行按钮。

### 2.5 Checkpoint 恢复与写后回查

完成的只读工具调用会保存 `input_hash`、`output_hash` 和结果回执。Checkpoint 恢复时，如果工具名称、租户和输入完全相同，可复用之前已经通过结果校验的读取结果。

写操作超时后状态记为 `unknown`，系统不会自动重放。`RecoveryManager` 会拒绝恢复，直到调用方提交权威回查结果。回查确认“已经写入”时复用既有结果；确认“没有写入”时才允许后续重新规划。

## 3. 协议变更

- `TaskState`：1.0 -> 1.1，新增重试预算、执行回执、可靠性事件和 `needs_attention`。
- `FailureEnvelope`：1.0 -> 1.1，保留旧 UI 分类并新增标准故障分类、错误签名和重试信息。
- `ToolSpec`：新增 2.0 生命周期协议。
- `Run Bundle`：2.0 -> 2.1，新增独立 `reliability.json`。
- 协议清单发布标识：`v36-recoverable-execution`。

旧 Checkpoint 中没有的新字段都有安全默认值，可以继续读取。

## 4. API 与页面

- `GET /api/reliability/status?task_id=...`：按当前可信身份的租户返回熔断和死信状态。
- `GET /api/reliability/tool-contracts`：返回全部 ToolSpec 2.0 声明。
- 运维台新增 `Reliability` 标签，展示任务预算、熔断、死信、回执和工具协议。
- Run Bundle 2.1 同时导出任务可靠性状态和 SHA-256 文件清单。

## 5. 验收方式

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v36
pytest -q
python scripts/run_v36_mvp_gate.py
python scripts/run_v36_reliability_acceptance.py
```

可靠性验收注入超时、429、502、Schema 错误和 worker crash，并检查：重试必然收敛、任务总预算不被突破、写操作不重复、未知写必须回查、死信按租户隔离。

## 6. 诚实边界

v36 的 Dead Letter Queue 使用本地 SQLite，熔断器状态位于当前服务进程。它适合单机面试 Demo 和本地可靠性验证。多实例部署所需的共享队列、租约、Fencing Token、Outbox 和跨实例原子性属于 v38，不能用本版结果冒充生产级分布式保证。
