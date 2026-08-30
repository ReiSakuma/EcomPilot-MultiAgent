# UI Surfaces

EcomPilot 使用同一套 Multi-Agent API 提供两个独立界面，避免把工程内部概念直接暴露给
电商用户。

## 用户工作台

入口：`/`、`/demo`、`/user`

用户填写商品类别、成本、售价、库存、最低毛利、目标人群和已确认功能。第一次提交只生成
草案并完成风险审核；只有用户查看方案并再次确认后，Browser Agent 才能同步模拟店铺。
用户工作台要求 DeepSeek 和 Playwright 均处于真实模式，不会静默使用确定性模型或 Mock
Browser 代替用户服务。

用户页调用受保护的 `/user/tasks/run` 与 `/user/tasks/{task_id}/resume`。这两个接口会在每次
请求前重新检查完整联动运行时；普通 `/tasks/*` 接口仅保留给离线自动化测试和受控开发工具，
不在运维页面暴露。

页面展示：

- 商品页面方案
- 定价、优惠、毛利和库存
- 市场参考摘要
- 风险与修改建议
- 店铺同步和字段级回读结果

## 运维监控台

入口：`/ops`

这是只读观察界面，保留 Agent 节点、Tool Record、Context、Model Fallback、Browser
Artifact、Checkpoint 和 Raw State。V21 的 Market 页还展示 Text-to-SQL 语句、策略结果和
只读标志，A2A 页展示能力目录、委派、预算和 Artifact 血缘。

运维监控台不提供新建任务、审批、恢复任务、运行回归或重置店铺按钮，也不会向任务、恢复、
评测或店铺重置接口发送 POST 请求。生成方案、确认方案和同步店铺只能由用户工作台发起。

运维页通过 `task_id` 绑定用户任务，并轮询 Checkpoint 与 Seller Center；用户生成和确认后，
节点、模型调用、浏览器执行和店铺状态会自动更新。

这种边界把业务命令和监控查询分开：运维人员可以定位问题，但不能在观察页面替用户产生新的
业务行为。故障恢复能力若后续需要，应放入独立的高权限维护工具并记录审计日志。

## Trace 与外部系统

- `/traces`：按 Run 展示模型、工具、状态转换、错误和耗时证据。
- `/seller-center`：模拟外部商家后台，不是用户工作台，也不代表已接入真实电商平台。

两个页面都会定时刷新。用户工作台会把当前 `task_id` 和 `run_id` 写入运维与 Trace 链接，
确保查看的是同一次用户行为。

离线跨页联动合同可用 `python scripts/run_linked_ui_contract.py` 验证。该脚本只证明页面共享
同一任务、运行记录和店铺状态，输出会明确标记运行状态被替换；它不属于真实 LLM 证据。

## 严格联动启动

配置好 DeepSeek 和 Playwright 后使用：

```bash
python scripts/run_linked_service.py
```

启动器会检查 Provider、Key、Market ReAct、四个 LLM Agent、`fail_closed`、Playwright 和
Chromium。任何真实服务未就绪都会拒绝启动，防止把规则模式误认为真实用户服务。

服务启动后，在另一个终端执行真实跨页验收：

```bash
python scripts/run_live_linked_ui_check.py
```

该脚本不替换状态、不改写接口，会真实产生至少五次 DeepSeek 调用，并检查 Market
Text-to-SQL、只读 SQL 策略、审批子运行、Playwright 执行和字段回读。输出必须同时满足
`passed: true` 与
`runtime_status_stubbed: false`，才能作为真实用户链路证据。
