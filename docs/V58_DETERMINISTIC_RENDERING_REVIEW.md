# EcomPilot v58：确定性渲染、审核一致性与修正审计

## 本版目标

v57 已允许模型动态提出策略候选，但模型理由仍可能把 10 元券写成 10% 或自行编造到手价。v58 把“经营创意”和“不可出错的数字”正式拆开：模型提出候选、目标和非数字理由；程序与工具唯一拥有价格、促销、到手价、毛利和库存。

## 核心实现

### 1. 确定性 Strategy Renderer

`app/safety/strategy_rendering.py` 从已选候选、任务约束、`calculate_margin` 和 `check_inventory` 重新投影最终策略。模型理由中含数字、百分号、金额、件数或期限的句子不会进入最终文案。最终 `launch_plan` 的每个数字都来自受信字段。

Strategy Artifact 新增 `strategy_render_version`、逐字段 `numeric_ownership`，以及记录输入和输出 SHA-256 的 `render_manifest`。

### 2. 局部内容修正

Listing 在生成 Artifact 前运行确定性语义归一化。未确认功能、未确认商品形态、衍生效果承诺和绝对化营销词只删除或重写对应字段，不重跑 Strategy，也不终止整个任务。

每条修正记录包含字段、修正前后值、原因、证据引用、前后内容哈希和稳定 `correction_id`。

### 3. Review 职责收窄

Review 只审核事实、宣传、候选引用和跨 Artifact 一致性。v58 Strategy 会在审核前再次按同一模板投影；模型提出的毛利或库存重算意见不会触发 Strategy 模型返工。审核结果新增 `execution_projection` 检查。

### 4. 执行绑定

`ExecutionPlan` 新增 `source_artifact_hashes` 和 `payload_hash`。Browser Agent 执行前核对来源 Artifact；载荷被篡改会在 Pydantic 校验阶段失败。浏览器执行结果回传同一载荷哈希，便于和 Seller Center 回读证据对照。

## 可观察性

运维后台的 Strategy 页展示数字所有权和渲染哈希，Review 页展示执行来源与载荷哈希。修正记录继续写入 `semantic_correction` Trace，并随 TaskState 进入 Run Bundle。

## 验收

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v58
python scripts/run_v58_acceptance.py
python -m pytest -q tests/test_v58_strategy_rendering_review.py
python -m pytest -q
```

离线验收不调用真实 DeepSeek；真实模型验证需显式配置 provider 和 API Key，不能把 Mock 结果冒充真实模型。

最终离线回归为 `526 passed in 47.90s`。桌面端 `1440x960` 与移动端 `390x844` 的用户页、运维页均无横向溢出、无页面脚本错误，截图与报告保存在 `reports/v58/visual/` 和 `reports/v58/visual_check.json`。

## 已知边界

本版绑定的是模拟 Seller Center 的执行计划和回读结果。对接真实平台时，还需要让外部平台保存幂等键、请求摘要或业务版本号，才能在平台侧形成同等级别的端到端证据。
