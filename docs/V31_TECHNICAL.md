# EcomPilot v31 技术文档

## 1. 版本目标

v30 已能把一次上架任务沉淀为可追踪的商品实体，但还不能回答“商品上架以后卖得怎么样”。v31 增加一条独立的只读分析链路，让会话、商品身份、历史指标和 Agent 工具选择形成闭环。

本版本仍是面试演示系统。没有配置真实电商平台 Connector 时，销量和订单是确定性生成的模拟数据，不代表真实经营结果。页面和 API 必须展示 `synthetic_demo`、查询期间和数据更新时间。

## 2. 主干结构

```text
用户消息
  -> RequestCompiler：识别 product_performance，确定时间范围
  -> EntityResolver：在当前租户 Product Ledger 中解析商品
  -> 只读 Analytics DAG：仅包含 analytics_agent 一个节点
  -> Analytics Agent bounded ReAct
       -> get_sales_metrics（必选）
       -> compare_sales_periods（可选）
       -> get_campaign_performance（可选）
       -> get_inventory_history（可选）
  -> AnalyticsArtifact
  -> CopilotResponse 1.3 / 销售表现面板 / Trace / 运维后台
```

严格 DAG 只负责任务边界和故障收敛；DAG 内部的 Analytics Agent 可以在最多四个低风险工具中做有限探索。它必须先取得基础销售指标，每个工具最多调用一次，总调用预算为四次；累计三次后进入强制收尾阶段，不能调用任何写工具。

## 3. 数据模型

- `daily_product_metrics`：按天保存曝光、点击、订单、销量、销售额、退款和期末库存。
- `campaign_metrics`：保存活动周期、优惠、投入、销量、销售额和 ROI。
- `inventory_movements`：保存初始库存、销售扣减、退款回补、调整和补货流水。

三张表都以 `tenant_id + product_id` 为查询边界，并保存 `source_type` 与 `source_updated_at`。外键指向 Product Ledger，商品删除时关联指标随之删除。

## 4. 为什么数字不交给模型生成

LLM 适合判断“这个问题需不需要同比、活动或库存证据”，不适合充当账本。v31 的最终销量、销售额、转化率、库存和变化率全部从工具结果组装。即使模型最终解释里引用了没有执行的工具，系统也只采用真实调用记录。数据缺失时节点失败并返回统一错误，不生成猜测数字。

## 5. 权限和隔离

- `analytics_agent` 的 A2A 能力是 `analytics.read`。
- Capability Token 只授权四个分析工具，工具均为 `RiskLevel.low`、`side_effect=false`。
- 工具从可信执行上下文读取租户，不接受模型传入 `tenant_id`。
- 其他租户即使知道商品 ID，也查询不到指标。
- 销售表现请求不会出现 Listing、Strategy、Review 或 Browser 节点，不需要审批，也不会修改模拟店铺。

## 6. 时间语义

日期计算由确定性程序完成，不交给模型自由推算：

- “最近 7 天 / 最近 30 天”：包含当天的连续自然日。
- “上个月”：上一个完整自然月。
- “本月”：本月 1 日至当天。
- “环比 / 对比 / 趋势”：与前一个等长周期比较。
- “活动前后”：允许 Analytics Agent 查询活动表现。

## 7. 用户与开发者界面

用户端销售表现页展示商品、查询期间、销量、销售额、订单、曝光、点击、转化率、退款、期末库存、可选对比或活动证据、调用工具、来源类型和更新时间。用户不会看到 Prompt 或原始共享状态。

开发者仍可在运维后台和 Trace 页面查看模型调用、工具调用、A2A Delegation、Artifact 哈希、失败协议和上下文用量。

## 8. 验收标准

- 能从“上次那个耳机”解析当前会话商品并查询最近 30 天表现。
- 用户端数字与 SQLite 同期聚合结果逐字段一致。
- 数据来源和更新时间覆盖率 100%。
- 跨租户泄漏计数为 0。
- Analytics Agent 的副作用工具调用数为 0。
- 数据缺失或工具失败时不输出虚构趋势。
- 全量历史回归测试通过。

## 9. 本版未做

- 没有真实淘宝、京东、Shopify 等订单 Connector。
- 没有自动改价、自动补货或自动调整广告。
- 没有预测模型训练；模拟指标只用于稳定演示和接口验收。
- 没有开放通用 Text-to-SQL 给 Analytics Agent；四个强类型工具更容易验证字段、权限和数值来源。
