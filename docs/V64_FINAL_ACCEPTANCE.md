# v64 真实 DeepSeek 与最终验收

## 1. 版本定位

v64 不改变商品上新的业务协议，而是把 v40 至 v63 已实现的对话任务、语义编译、安全门禁、市场清洗、Strategy ReAct、确定性核算、Review、审批写入、记忆与恢复纳入同一套可审计验收。

最终状态只有三类：

1. `passed`：全量离线回归、故障注入和真实 DeepSeek 必要门禁全部通过。
2. `offline_passed_live_not_observed`：代码与本机故障控制通过，但没有观察到真实 DeepSeek 成功报告。
3. `failed`：任一已执行的必要门禁失败。

`not_observed` 不是通过。它表示该场景没有发生，不能用于证明恢复能力。

## 2. 统一指标

`app/eval/stability.py` 直接读取任务证据，不解析页面文字：

- 正常上新端到端成功率，目标不低于 95%。
- Strategy 逻辑模型调用 P95，目标不超过 4 次。
- Strategy 阶段上下文相对原始上下文的 P95 缩减率，目标不低于 35%。
- 证据工具超过 0 至 2 个导致的任务失败数，目标为 0。
- 已存在合格候选时，选择阶段导致的失败数，目标为 0。
- 已注入输出截断时的恢复率，目标为 100%。
- 所有降级是否带错误码、阶段和 Trace/开发证据。
- 模型记录是否来自非 deterministic Provider，并使用 `actual` 用量来源。

正常任务只统计 `normal_listing`。高价等待确认、缺信息和业务拒绝是正常业务终态，不会被错误计入技术失败率。

## 3. 两类测试为什么分开

### 真实 API 套件

`scripts/run_v64_live_deepseek_suite.py` 用真实 DeepSeek 检查模型协议、真实 usage、上下文、调用预算和端到端行为。`smoke` 适合每次修改后运行；`full` 用于最终验收。

### 故障注入套件

`scripts/run_v64_fault_injection.py` 使用受控适配器稳定复现 429、超时、工具失败、候选截断、Review 截断、长工具结果、并发重复提交和任务恢复。它验证系统面对故障的控制逻辑，不伪称公网服务实际发生过故障。

## 4. Run Bundle 新证据

单任务 ZIP 的 `verification_matrix` 新增：

- `strategy_stage_context_projection`
- `strategy_logical_model_call_budget`
- `strategy_candidate_finalization`
- `degradation_traceability`

原有 EvidencePlan、ReAct Context Budget、SQL 策略、沙盒、A2A、审批、浏览器回读和权限证据继续保留。

## 5. 目标覆盖矩阵

`scripts/run_v64_final_acceptance.py` 汇总十二类目标：

1. 会话与多任务 Checkpoint 隔离；
2. 多意图和多商品批任务；
3. 真实模型语义编译与错别字容错；
4. 安全和业务前置门禁；
5. 市场异常样本清洗与价格门禁；
6. Strategy 自主证据选择、工具核算和候选收尾；
7. 多 Agent A2A 与最小权限；
8. 记忆、可信摘要和上下文压缩；
9. 用户确认、幂等写入和浏览器回读；
10. Text-to-SQL、租户过滤和进程沙盒；
11. 重试、恢复、并发和原子性；
12. Trace、Run Bundle 和只读运维。

矩阵中的通过来自全量测试，不替代真实 API 门禁。

## 6. 运行顺序

```bash
python -m pytest -q
python scripts/run_v64_fault_injection.py
python scripts/run_v64_live_deepseek_suite.py --profile smoke
python scripts/run_v64_live_deepseek_suite.py --profile full
python scripts/run_v64_final_acceptance.py --reuse-regression
```

如果没有 Key，真实脚本退出码为 2 并生成 `external_blocked` 报告。Key、Authorization 和 Secret 字段不会进入报告。

网页联动测试应在相同环境变量下设置 `ECOMPILOT_BROWSER_BASE_URL=http://127.0.0.1:8475` 并运行 `python scripts/run_linked_service.py`，避免误连仍以 deterministic 模式运行的旧进程。

## 7. 最终边界

本版完成的是可运行、可恢复、可解释的面试参考系统。模拟 Seller Center 和本地 Playwright 证明写入协议与回读校验，并不等于已经适配淘宝、京东或 Shopify 的真实商家后台；SQLite 的原子事务也不等于跨机房分布式高可用。
