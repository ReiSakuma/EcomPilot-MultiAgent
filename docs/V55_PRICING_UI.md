# v55 用户价格确认与只读运维证据

## 1. 本版目标

v54 已经能在目标售价明显偏离核心市场时暂停工作流，但主要依赖文字回复。v55 把这个能力做成完整产品交互：用户能看懂为什么暂停并选择下一步，开发者能在只读后台复核证据，两个页面使用同一个 `task_id`、`run_id` 和 `checkpoint_version`。

本版不改变价格统计算法，也不重构 Strategy。它只完成价格确认协议、用户操作、运维证据和对应验收。

## 2. 用户看到什么

价格门控要求确认时，页面展示：

- 用户目标售价与核心可比参考价。
- 默认接受区间和相对偏离比例。
- 核心可比、相邻档次、全市场三层价格区间。
- 核心、相邻和全市场样本数，以及被排除脏样本数。
- 证据可信度和同时满足市场位置、最低毛利的建议区间。

用户有三个动作：

1. `adopt_suggested_price`：采用建议价格，沿原任务恢复 Listing、Strategy 和 Review。
2. `keep_original_with_evidence`：提交至少 4 个字的可核验差异化依据后保留原价，依据写入恢复审计。
3. `market_analysis_only`：结束上新流程，只保留市场分析，不生成方案、不请求审批、不修改店铺。

三个动作统一调用 `POST /api/copilot/tasks/{task_id}/price-confirmation`。请求必须携带当前 checkpoint 版本和幂等请求 ID。版本过期返回 `409`，依据不足返回 `422`，不会静默创建另一项任务。

## 3. 协议与状态

`CopilotResponse` 升级为 `1.7`，新增 `PriceConfirmationPrompt`：

- 任务身份：`task_id`、`run_id`、`checkpoint_version`。
- 价格判断：`position`、`target_price`、`core_reference_price`、`deviation_rate`。
- 三层证据：`core_price_band`、`adjacent_price_band`、`full_market_band`。
- 决策质量：`evidence_quality`、各层样本数、排除样本数。
- 三个结构化 `PriceConfirmationOption`。

系统恢复时使用原 TaskSession，新建子 Run 并推进 checkpoint。Market Artifact 的内容哈希保持不变，`build_market_report` 工具调用次数仍为 1。重复或过期确认由 checkpoint 乐观锁和会话请求幂等共同阻止。

选择“只看市场分析”后，外部结果为 `read_only_completed`，用来和普通上新成功区分。

## 4. 进度和结果语义

用户进度增加“清洗市场数据”，顺序为：

```text
清洗市场数据 -> 调研市场参考 -> 检查目标售价与市场位置
              -> 商品页面 -> 定价促销 -> 风险检查 -> 店铺同步
```

暂停时前三项完成，后续阶段保持 `pending`，界面显示“尚未执行”。它不是技术失败，也不是业务拒绝。

界面状态分开处理：

- `waiting_for_input`：黄色，等待用户作出业务选择。
- `business_rejected`：黄色，业务条件需要修改。
- `technical_failed`：红色，系统组件或协议执行失败。
- `completed/read_only_completed`：正常完成语义。

## 5. 只读运维后台

运维页不能创建、审批、恢复或执行任务，只能读取用户工作台已经产生的状态。Market 标签展示：

- 价格门控状态、位置、原因码、偏离率和建议区间。
- 核心可比、相邻档次、全市场的样本数、评论数和价格统计。
- 清洗前后数量、保留比例、警告和内容哈希。
- 被排除脏样本的原价、归一化价格、统计标记和原因码。
- 被保留的极端但合理样本及业务解释。
- 非核心样本的分层、匹配分数和不匹配原因。
- 总览中的 `run_id`、`parent_run_id`、`resume_count` 和 `checkpoint_version`。

这些都是可审计业务证据，不展示模型内部思维链。

## 6. 验收结果

已完成：

- `tests/test_v55_pricing_ui.py`：5 项测试，覆盖三种动作、原任务恢复、市场复用、状态语义和前后台职责边界。
- `scripts/run_v55_acceptance.py`：11 项机器断言全部通过。
- `scripts/run_v55_visual_check.py`：桌面 `1440x960`、移动 `390x844` 均无横向溢出、无页面错误，三个动作可见。
- 全量回归：`502 passed in 49.40s`。

机器报告位于 `reports/v55/`，最终截图位于 `reports/v55/visual/`。

## 7. 已知边界

- v55 的建议价格由确定性门控根据核心市场与毛利底线形成，不是 Strategy 候选方案。
- 保留原价所提交的依据会留痕，但真实商品事实仍需后续数据源或审核能力验证。
- 促销单位协议尚未重构，10 元券、10% 与九折的类型化隔离属于 v56。
- Strategy 动态提出候选、工具批量裁决和单候选淘汰属于 v57。
- 视觉验收使用确定性任务状态和本地字体，不声称调用了真实 DeepSeek；真实模型需按 README 单独验证。
