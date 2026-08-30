# V59 最终整合与验收

## 1. 版本定位

v59 不再增加新的业务 Agent，而是把 v51-v58 的定价与 Strategy 重构收敛成可运行、可恢复、可审计、可面试讲解的最终参考实现。

最终状态分为三层：

1. `interview_ready`：全量离线回归、兼容、浏览器和 Run Bundle 证据通过。
2. `real_external_chain_validated`：真实 DeepSeek 与真实 Playwright 记录满足严格证据规则。
3. `production_ready`：固定为 false；真实平台、生产 IdP 和跨主机基础设施不在本项目声明范围内。

## 2. 最终主链路

1. Semantic Compiler 用模型把自然语言编译成结构化业务字段。
2. Preflight 先检查缺失字段、矛盾、指令注入、虚假宣传和基础毛利可行性。
3. Market Agent 查询样本，统计程序完成归一化、异常检测与核心/相邻/全市场三层分类。
4. Market Price Gate 只使用核心可比层判断用户价格是否落在集中阈值内。
5. Listing Agent 生成文案，并对未确认宣传做字段级修正。
6. Strategy Agent 动态提出 2 至 4 个候选；工具计算到手价、毛利和库存并淘汰失败候选。
7. 模型只能从合格候选中选择；程序用受信数字确定性渲染最终策略。
8. Review 检查事实、表达、候选引用和跨 Artifact 一致性，不重新计算业务数字。
9. 用户确认后，Browser Agent 验证 Artifact 与 ExecutionPlan 哈希，写入模拟店铺并回读。

## 3. V59 新增

### 3.1 兼容诊断

`app/release/compatibility.py` 对旧 Checkpoint 返回三种结果：

- `compatible`：可以直接继续。
- `migrated`：通过只读迁移继续，原文件不覆盖。
- `requires_regeneration`：状态不安全，保留会话，从对话历史重建任务。

旧版金额券会迁移到 `PromotionSpec 1.0`；单位无法确认的旧优惠不会猜测。

### 3.2 执行身份联动

ExecutionPlan 新增 `task_id`、`run_id` 和源 `checkpoint_version`。模拟店铺快照保留最近一次写入身份；`/api/tasks/{task_id}/linkage` 同时核对用户页、运维页、Trace 与店铺写入来源。

最终 Checkpoint 可能晚于执行计划源 Checkpoint，因此联动报告同时展示“当前状态版本”和“已审核执行版本”，不会把两个生命周期数字强行伪装成相同。

### 3.3 Run Bundle 2.5

每个 ZIP 除原状态、Trace、A2A、安全账本、可靠性记录与附件外，新增：

- 市场数据清洗证据。
- 三层可比性分类。
- 价格门控结论。
- 动态候选生成。
- 工具裁决和合格候选数。
- 模型最终选择。
- 确定性渲染与数字所有权。
- 局部修正审计。
- 浏览器写入与回读。
- 四个页面的任务身份联动结果。

### 3.4 真实模型门禁

`validate_live_deepseek_report()` 要求每次运行至少有一次模型调用，每条记录均为 DeepSeek 完成调用，Token 来源为 API 的 `actual`，并且没有 `model_fallbacks`。任何 deterministic、Mock、估算用量或静默降级都会使真实链路验收失败。

## 4. 可靠性设计

- 阶段超时：模型、ReAct、节点和工作流分别有预算，避免一小时悬挂。
- 重试：瞬时错误和限流最多三次，使用退避；Schema 错误只允许受控修复。
- 熔断：同一错误连续达到阈值后打开，恢复窗口后半开探测。
- 幂等：店铺写入以任务和操作构成幂等键；重复确认不重复写入。
- 并发：Checkpoint 乐观版本、执行租约和 fencing token 阻止旧 Worker 覆盖新状态。
- 恢复：保存节点级 Checkpoint；可恢复错误进入等待或重试，预算耗尽进入人工处理。
- 五种外部终态：成功、降级成功、等待用户、业务拒绝、人工处理。

## 5. 验收矩阵

| 范围 | 命令 | 证据 |
| --- | --- | --- |
| 全量回归 | `python -m pytest -q` | Pytest 结果 |
| 三种终态和 Bundle | `python scripts/run_v59_acceptance.py` | `reports/v59/*.json` 与三个 ZIP |
| 页面与联动 | `python scripts/run_v59_browser_acceptance.py` | 截图和 `browser_acceptance.json` |
| 真实模型七场景 | `python scripts/run_v59_live_deepseek_suite.py` | `live_deepseek_suite.json` |
| 发布总状态 | `GET /api/release/v59` | 分阶段状态与边界 |

真实七场景包括正常价格、明显偏高、改价恢复、高价有依据、动态候选、单候选失败和全部候选失败。报告保留模型次数、provider/model、实际 Token、工具调用、耗时和降级记录。

## 6. 面试说明与边界

语言模型负责提出方案和解释，金额、百分比、库存属于必须可复算的确定性事实。Market、Listing、Strategy、Review、Browser 权限和失败边界不同；Review 独立于创作者，Browser 只接受审核后的哈希计划。主业务顺序和安全门禁固定，Agent 内部保留有界 ReAct；返工环有最大迭代数和强制收尾条件。

市场数据和 Seller Center 是本地演示数据。真实平台连接、生产身份系统、分布式数据库和跨主机熔断需要在生产化项目中替换。
