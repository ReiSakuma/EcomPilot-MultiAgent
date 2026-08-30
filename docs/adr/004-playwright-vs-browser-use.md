# ADR 004: 浏览器执行选择 Playwright

## Context

Seller Center 写操作需要真实 DOM 行为，但动作范围固定且副作用风险高。

## Decision

使用 Playwright 和稳定 test id 执行固定 ExecutionPlan，不使用 VLM 或自由点击 Agent。

## Alternatives

Browser-use 或 VLM 更适合未知页面探索，但动作不确定、成本更高、恢复语义更难定义。

## Consequences

动作可审计、可截图、可字段回读；代价是 selector drift 需要人工维护。

## Evidence

Browser Eval 覆盖审批、幂等、Ticket 完整性和回读；两个 DOM/脚本故障有回归测试。

## Revisit Trigger

页面高度动态且固定 selector 维护成本不可接受时，评估受限 VLM 定位，但保留动作白名单。
