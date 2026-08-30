# V30 商品身份与历史追踪技术文档

## 1. 版本目标

v29 已经能把自然语言分流到上新、市场调研、任务状态和普通问答，但“任务”和“商品”仍是分离的。v30 补上稳定商品身份，使用户执行一次上新后，可以在同一会话继续询问“查看这个商品详情”，也可以用 SKU、商品 ID 或任务 ID 找回它。

本版只解决商品身份、详情和执行历史，不伪造销量数据，也不引入向量数据库。

## 2. 主干结构

```text
用户消息
  -> RequestCompiler（识别 product_detail 等意图）
  -> LangGraph 路由
  -> EntityResolver（确定性商品解析）
  -> Product Ledger（租户隔离查询）
  -> 商品档案 + 商品时间线

上新确认执行
  -> BrowserAgent 写入并回读 Seller Center
  -> ProductLedger.record_successful_execution()
  -> 同一事务写入 Product / Alias / TaskLink / Timeline
  -> 更新 Conversation.active_product_id
```

## 3. 数据协议

SQLite Schema v3 新增四张核心表：

- `product_ledger`：商品主档，保存商品 ID、SKU、标题、类别、状态、来源任务和店铺快照。
- `product_aliases`：可查询别名，包括商品 ID、SKU、标题和类别。
- `task_product_links`：连接 Task、Product、Conversation 和 Artifact 引用。
- `product_events`：只追加的商品时间线，记录方案创建、受控修订、审核、店铺同步、发布和促销。

API 响应协议升级到 `1.2`，新增 `entity_refs`、`product` 面板和 `timeline` 面板。

## 4. 实体解析策略

解析顺序是 `product_id -> SKU -> task_id -> 会话 active_product -> 标题/类别候选`。前三种是结构化标识，可靠性最高；名称模糊匹配只用于生成候选。

当只命中一个商品时直接返回。当命中多个商品时，LangGraph 使用 `interrupt()` 暂停当前线程，向用户列出候选；用户回复序号、SKU 或商品 ID 后，通过原 checkpoint 恢复。最多允许三轮选择，禁止模型自行猜测。

## 5. 权限与一致性

- Product Ledger 的每个主键和查询都包含 `tenant_id`。
- 默认查询排除 `status=deleted` 的商品。
- 只有 BrowserAgent 回读验证 `verified=true` 后才登记商品。
- 商品、任务、Artifact、时间线和会话活动商品在一个 `BEGIN IMMEDIATE` 事务中写入。
- 时间线使用幂等键，重复审批或重放不会重复制造事件。

## 6. 页面与 API

- 用户工作台：`http://127.0.0.1:8131/`
- 运维只读后台：`http://127.0.0.1:8131/ops`
- Trace：`http://127.0.0.1:8131/traces`
- 模拟商家后台：`http://127.0.0.1:8131/seller-center`
- 商品列表：`GET /api/copilot/products`
- 商品详情：`GET /api/copilot/products/{product_id}`

成功同步后，右侧新增“商品档案”和“商品时间线”。用户后续输入“查看这个商品详情”，无需再次执行上新工作流，也不会修改店铺。

## 7. 真实 DeepSeek 验证

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v30
cp .env.example .env
```

在 `.env` 配置 `ECOMPILOT_LLM_PROVIDER=deepseek`、`ECOMPILOT_LLM_MODEL=deepseek-v4-pro`、`DEEPSEEK_API_KEY`，并设置 `ECOMPILOT_BROWSER_BACKEND=playwright` 后运行：

```bash
python scripts/run_linked_service.py
```

先完成一次上新并确认同步，再在同一会话发送“查看这个商品详情”。预期第二次请求走 `product_detail` 只读路径，展示同一商品 ID 和时间线，不产生店铺写入。

## 8. 验收口径

- 单候选引用准确率不低于 98%。
- 多候选场景 100% 触发用户选择。
- 跨租户商品和已删除商品 100% 不可见。
- 成功执行后可从 Conversation、Task 和 Seller Center 快照定位同一商品。

运行 `python scripts/run_v30_acceptance.py` 会生成 `reports/summaries/V30_ACCEPTANCE.json` 和 `reports/summaries/V30_ACCEPTANCE.md`。
