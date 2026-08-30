# EcomPilot MultiAgent V38 技术说明

## 版本目标

V38 解决的不是“让 Agent 再多想一步”，而是一个更基础的生产问题：重复点击、多个 Worker、进程掉线和同商品并发修改时，系统必须只确认一次可解释的业务效果。

版本号是 `0.38.0`，发布标识是 `v38-concurrency-control`。

## 请求与 Worker 解耦

用户提交消息后，API 先把 `copilot_turn` 写入 SQLite WAL 持久化队列，再返回 SSE 地址。演示进程会自动唤醒兼容 Worker；独立进程也可以运行：

```bash
python scripts/run_v38_worker.py
```

Worker 不能直接“拿走”任务，而是领取有期限的 `Lease`（租约）。租约包括 Worker ID、过期时间和单调递增的 `lease_token`。Worker 失联后，任务会重新排队；旧 Worker 恢复时，它的令牌已经过期，不能提交结果。

队列对全局深度和单租户深度执行背压，并使用每租户已服务次数进行公平选择，防止一个高流量租户长期占满 Worker。

## 资源级并发控制

每次店铺写入使用以下业务幂等身份：

```text
tenant_id + resource_id + operation + execution_plan_hash
```

四层控制共同生效：

1. `Idempotency Key`：同一方案重复提交只复用第一次确认结果。
2. `Optimistic Version`：提交时资源版本必须仍等于领取时版本。
3. `Fencing Token`：每次资源租约获得更大的令牌；旧令牌永远不能覆盖新令牌。
4. `Saga + Transactional Outbox`：执行状态、确认业务效果和待发布事件在同一数据库事务中写入。

这实现的是 `effectively-once`，即业务上可确认的一次效果，不宣称浏览器和外部平台之间存在跨系统 ACID 事务。若浏览器在提交后断线，Saga 会进入 `needs_attention`，不能盲目认定失败后重复写入。

Outbox Relay 可单独运行 `python scripts/run_v38_outbox_relay.py`。当前演示接收端是标准输出；替换为 Kafka、SQS 或 RabbitMQ 时，事件领取、过期接管和发布确认协议不变。

## 隔离工作池

V38 为 `workflow`、`model`、`sql`、`read_tool`、`write_tool` 和 `browser` 定义独立 Bulkhead（隔离舱）容量。Browser 池耗尽不会占走 SQL 池额度，某类慢依赖不会拖死全部请求。

SQLite 队列负责跨进程任务所有权；Bulkhead 负责单个 Worker 进程内的并发容量。生产环境可将同一协议映射为消息队列、Kubernetes Worker Deployment 和分布式信号量。

## 运维与证据

运维后台 `Concurrency` 页签展示：

- 各池排队和执行状态；
- 活跃 Worker 租约；
- Bulkhead 容量；
- Saga 状态与需人工处理数量；
- 已确认业务效果和待发布 Outbox 数量。

Run Bundle 2.3 会附带当前租户的并发运行快照。协议清单新增 `durable_job_queue 1.0`、`worker_lease_fencing 1.0`、`execution_saga_outbox 1.0` 和 `worker_bulkhead 1.0`。

## 验收场景

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v38
pytest -q tests/test_v38_distributed_runtime.py
python scripts/run_v38_concurrency_acceptance.py
python scripts/run_v38_mvp_gate.py
```

并发验收覆盖重复点击、租户公平、队列背压、Worker 丢失接管、旧 Worker 拒绝、同商品冲突、同方案单一效果、资源令牌过期、Saga/Outbox 原子落盘和 Browser/SQL 隔离。

## 已知边界

- SQLite WAL 是单机多进程参考实现，不是跨主机高可用消息队列。
- 生产部署应迁移到 PostgreSQL、Redis 和 Kafka/RabbitMQ/SQS 等共享基础设施。
- 模拟商家后台是项目内测试目标，不是真实电商平台。
- 浏览器外部效果无法与本地数据库组成全局事务，因此必须依赖幂等、回读验证、Saga 和人工关注状态。
