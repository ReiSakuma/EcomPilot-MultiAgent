# V35 五分钟面试演示

## 0:00-0:40 产品与会话

打开 `/user`，从自然语言创建上新方案，再追问同一商品的销售表现。说明会话、商品和任务具有
稳定 ID，刷新或重启后仍可追溯，不是一次性表单。

## 0:40-1:40 Agent 自主决策

从用户工作台运行一个启用 DeepSeek 的任务，在只读 `/ops` 和 `/traces` 展示模型调用、
ReAct 的工具选择、Observation 回传和下一步动作。强调模型选择工具，但策略网关决定它是否
有权执行，运维页面只能观察，不能替用户新建或审批任务。

## 1:40-2:40 多 Agent 与安全边界

打开 `A2A 协作`，展示 Structured Handoff、Artifact 引用、Capability Token、工具范围和
安全账本。解释多个 Agent 的价值是职责与权限隔离，不是角色名称多。

## 2:40-3:30 SQL 与沙盒

打开 `Market`、`Sandbox`、`Access`，展示模型生成只读 SQL，SQLGlot AST 加租户过滤，
随后在无 API Key 环境、有限 CPU/内存和硬超时的独立进程中执行。

## 3:30-4:20 浏览器副作用

打开 `Execution` 和 Mock Seller Center，展示人工审批、一次性票据、Playwright 填表、
提交、回读验证、截图路径和租户分区。说明这是项目自建店铺，不是真实电商平台账号。

补充展示一次可修复文案：Review 指出 Strategy 的无依据表述后，系统只清理
`strategy.launch_plan` 并复审，已经由工具验证的价格、毛利和库存保持不变。

## 4:20-5:00 最终证据

打开 `Release`：质量门禁应通过，11/11 风险有控制和测试，证据 SHA-256 有效；同时指出
“生产就绪”明确为否。最后运行：

```bash
python scripts/run_v35_mvp_gate.py
python -m pytest -q
```

最后导出当前任务的 Run Bundle v2，展示其中的 `conversation.json`、
`protocol_manifest.json` 与 `bundle_manifest.json`，证明演示结论可离线复核。

推荐结论：项目的亮点不是让 LLM 拥有无限权限，而是让它能自主决策，同时把每次决策约束
在可审批、可审计、可恢复和可测试的边界里。
