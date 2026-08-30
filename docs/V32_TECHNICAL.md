# EcomPilot v32 技术文档

## 1. 版本目标

v32 解决“所有问题看起来都经过同一条 Agent 流程”的问题。系统不让 LLM 临时创造工作流，而是由确定性 Conversation Orchestrator 根据已校验意图，从注册表选择一个版本化模板。这样既保留按需编排，也能事先审查每条写路径。

## 2. 路由结构

```text
用户消息
  -> Request Compiler：意图、实体提示、明确字段
  -> Conversation Orchestrator：选择 RoutePlan 1.0
  -> 已注册工作流模板
     create_listing      -> Market + Listing + Strategy + Review -> Approval -> Browser
     modify_listing      -> Entity Resolver + 字段变更计划 + 业务子图 -> Approval -> Browser
     market_research     -> Market
     product_performance -> Entity Resolver + Analytics
     product_detail      -> Entity Resolver + Product Ledger
     general_chat        -> Direct Answer
  -> CopilotResponse 1.4 / Trace / 运维 Routing 视图
```

`RoutePlan` 包含唯一 route ID、模板版本、意图、风险范围、计划和跳过的 Agent、能力范围、是否审批以及停止条件。模型可以在 Agent 内部进行有界 ReAct，但不能增加 RoutePlan 未声明的 Agent 或工具。

## 3. 商品字段级修改

`modify_listing` 只接受白名单字段：售价、库存、优惠金额和标题。系统先使用租户隔离的 Entity Resolver 找到商品，再读取 Seller Snapshot 作为底稿。Review 生成执行计划时只覆盖用户明确给出的字段，未出现在 `change_plan` 中的标题、卖点、价格、库存或优惠保持原值。

审批前 Browser 不执行写工具。审批后仍使用原 `product_id` 和 SKU，Product Ledger 新增 relation=`modified` 的任务关联和 `listing_revised` 事件，不会误建第二个商品。

## 4. 权限分层

- `read`：市场、销售、商品和任务查询。
- `write_plan`：Listing、Strategy 和 Review 可以生成候选方案，但不能改店铺。
- `write_execute`：只有 Browser Agent 使用，并且 Token 必须绑定当前 task、delegation、tenant、Agent、工具、会话、轮次和用户审批。

A2A 请求和 HMAC Capability Token 都携带权限等级。即使未审批的任务生成了 Browser 委派，Token 也带 `approval_granted=false`，调用 `browser_execute` 会被 Capability Authority 拒绝。

## 5. 动态与固定的边界

动态部分是“本轮选择哪个模板”和“模板内 ReAct 选择哪些允许工具”。固定部分是模板节点集合、写路径审批、最大迭代、工具白名单和停止条件。这个组合比自由生成 DAG 更容易测试，也避免模型把只读问题路由到写工具。

## 6. 可观测性

运维后台 Routing 页展示 RoutePlan 版本、route ID、模板、风险范围、本轮实际 Agent、明确跳过的 Agent、能力 scope、停止条件和 A2A 权限上下文。Trace 额外记录 `conversation_orchestrator.route_plan` 事件，可关联 conversation、turn、task 和 run。

## 7. 验收标准

- 市场查询只运行 Market，销售查询只运行 Analytics。
- 普通问答不创建业务任务或调用业务工具。
- 商品修改先解析已有商品，并产生字段级变更计划。
- 未审批写工具成功数为 0。
- 审批后只修改授权字段，商品身份保持不变。
- `write_execute` Token 未绑定审批时必须拒绝写工具。
- 路由、跳过 Agent、A2A 和权限决策均可追溯。

## 8. 本版未做

- 不允许 LLM 自由生成新 DAG、运行时创建 Agent 或跨模板跳转。
- 不支持删除商品、批量改价等破坏性操作。
- 不接真实电商平台；Browser 仍只操作 Mock Seller Center。
- 持久化 Merchant Memory 和 Context Engineering 2.0 留到 v33。
