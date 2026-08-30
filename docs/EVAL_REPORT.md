# Eval Report Guide

## 当前离线结果

| 项目 | 结果 |
|---|---:|
| Automated tests | 544 passed |
| Interview Eval | 40/40 |
| Hard constraint satisfaction | 100% |
| Unauthorized side effects | 0 |
| Tool reliability | 7/7 |
| Recovery | 7/7 |
| Mock browser | 4/4 |

消融固定样本结果：无 Schema 结构通过率 16.67%，有 Schema 为 100%；确定性 Review
违规漏过率为 0，宽松 LLM-only fixture 漏过率为 100%；Submit-only 对字段漂移检测率
为 0，Read-back 为 100%。这些数字用于证明机制，不代表真实模型总体质量。

## 外部证据状态

真实 DeepSeek 和 Playwright 属于外部联调 Gate，不混入离线通过率。运行
`scripts/run_v65_live_deepseek_selfcheck.py` 后，脚本会记录 Provider、实际 Token、调用次数
和失败类型；缺少 API Key 时明确标记 `external_blocked`，不会用 Mock 冒充真实调用。
浏览器视觉检查单独记录桌面、移动端、Console Error 和页面回读结果。

## 报告层次

- `reports/raw/*.json`：Case 级原始事实。
- `reports/raw/logs/*.log`：每阶段控制台输出。
- `reports/final_report.json`：机器可读聚合报告。
- `reports/summaries/FINAL_REPORT.md`：面试展示摘要。
- `reports/summaries/listing_blind_review.csv`：真实 LLM 完成后生成的盲评表。

## 指标解释

- Task Success：任务达到预期业务状态，不等于所有任务都必须写入成功。
- Constraint Satisfaction：毛利、库存和合规约束是否被正确执行。
- Structured Success：模型输出是否通过严格本地合同。
- Browser Verify：执行后观察状态是否逐字段匹配计划。
- Fallback：模型失败后是否发生确定性降级。
- P95 Latency：95% 模型调用不超过的耗时。
- Cost：按实际 Token 和已配置价格表估算，不含浏览器与基础设施成本。

## Provider 口径

- OpenAI：`structured_output_mode=strict_json_schema`。
- DeepSeek：`structured_output_mode=json_object_local_schema`。
- 两者最终都必须通过本地 Pydantic 校验；DeepSeek 的 JSON Object 成功率不能表述成
  Provider 端严格 JSON Schema 成功率。
- DeepSeek 费用按 2026-08-24 官方价格页的 cache-miss 单价保守估算，价格变化后应更新
  `app/model/pricing.py` 再生成报告。
