# Project Overview

## 一句话

EcomPilot 把电商新品上架拆成市场证据、文案生成、定价策略、硬约束审核和浏览器
执行，并用审批、幂等、页面回读、恢复和评测控制 LLM 与浏览器的不确定性。

## 用户与任务

目标用户是需要重复上架商品的小型电商运营团队。用户输入成本、售价、库存、目标
人群、销量和最低毛利要求；系统输出市场摘要、Listing、促销策略、审核结论和可执行
计划。获得人工审批后，Browser Agent 才能写入 Seller Center 并回读验证。

## 为什么是 Multi-Agent

不是因为角色越多越好，而是职责、Context、工具权限和失败恢复边界不同。Market
只能读取市场数据，Strategy 使用财务工具，Review 没有写权限，Browser 是唯一拥有
高风险写权限的角色。Listing 与 Strategy 可在 Market 完成后并行。

## 三条核心技术线

1. 不稳定 LLM 的受控接入：严格 Schema、本地校验、有限修复、调用预算和失败策略。
2. 高风险副作用安全：权限、人工审批、一次性 Ticket、幂等和页面回读。
3. 可诊断与可恢复：Trace、Checkpoint、局部重算、Bad Case 分类和冻结 Eval。

## 五分钟演示

1. 运行正常但未审批任务，展示 `waiting_for_approval` 且副作用为零。
2. 从 Checkpoint 审批续跑，只执行 Browser 节点。
3. 打开 Trace，展示执行、验证与幂等记录。
4. 运行 `scripts/run_interview_eval.py`，展示 40/40 与未授权副作用为 0。
5. 展示消融报告，解释三个 Guardrail 为什么存在。

## 不宣称的能力

不宣称生产店铺接入、实时市场抓取、分布式一致性、VLM 自由点击、GraphRAG、模型
训练或自动 Reflection Memory。
