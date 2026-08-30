# v54 市场价格门控与可恢复确认

## 本版目标

v54 在 v53 三层市场证据之后增加一个确定性的价格合理性检查。它只使用核心可比商品参考价，不让用户心理价反向影响市场选样。当目标售价明显偏离核心市场时，系统暂停在 Listing 和 Strategy 之前，等待用户选择，而不是把任务判成技术失败。

## 主流程

```text
语义编译与前置安全检查
  -> Market Agent（读取并清洗市场数据）
  -> Market Price Gate（纯程序计算，不调用模型）
     -> 区间内：Listing + Strategy -> Review -> 审批
     -> 区间外：waiting_for_input
        -> 采用建议价格：新 Run 从价格门继续
        -> 保留原价并提供依据：记录 override 后继续
        -> 只看市场分析：跳过后续写方案并只读结束
```

## 关键协议

- `MarketPriceAssessmentInput`：目标价、成本、最低毛利、核心参考价、样本量和证据质量。
- `MarketPriceAssessment`：位置、偏离率、接受区间、毛利底价、建议区间、原因码和覆盖依据。
- `MarketPriceAssessmentArtifact`：在 A2A 和 State Reducer 中保存价格判断的版本、来源和哈希。
- 默认标准档阈值为核心参考价上下 `15%`；标准商品可配置 `10%`，差异化商品可配置 `25%`。
- 低质量或多峰证据只提供建议，不形成硬暂停。

## 暂停与恢复

首次偏离时，Market 和价格门完成，Listing、Strategy、Review、Browser 保持 `pending`。会话仓库保存任务 ID、Run ID、Checkpoint 版本和评估快照。

用户确认后：

1. 创建新的 `run_id`，原 Run 写入 `parent_run_id`。
2. 保留 Market Artifact 和市场工具记录。
3. 只清理价格门及其下游 Artifact，再从价格门重新运行。
4. 用旧 Checkpoint 版本作为并发条件；重复请求由会话 `client_request_id` 返回同一响应，过期确认由 `StaleCheckpointError` 拒绝。
5. 在 `recovery_history`、Trace 和约束中保存选择、确认请求 ID 与用户依据。

## 用户可见行为

等待时回答会说明核心参考价、接受区间、最低毛利形成的建议区间，并提供三种文字选择。当前版本已经能在同一会话中识别这些回复并续跑同一个任务。专用按钮、三层价格可视化和只读运维证据属于 v55。

## 验收

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v54
python scripts/run_v54_acceptance.py
pytest -q tests/test_v54_market_price_gate.py
pytest -q
```

机器可读报告写入 `reports/v54/v54_acceptance.json`。

本版本验收结果：12 项机器断言全部通过，完整测试套件 `497 passed`。
