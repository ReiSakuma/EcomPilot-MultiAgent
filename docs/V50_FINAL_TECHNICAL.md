# V50 最终整合版技术文档

## 版本定位

v50 是当前面试项目路线的功能冻结版本。它不宣称已经接入真实生产电商平台，也不把 SQLite、Mock Seller Center 或离线模型桩包装成生产能力。其目标是让架构、用户体验、测试报告和面试陈述指向同一套可复现事实。

## 整合后的主干

```text
用户自然语言
  -> LLM-first Semantic Compiler
  -> 安全预检与缺失字段检查
  -> 多意图/多商品任务路由
  -> 每个 TaskSession 独立 Checkpoint
  -> Market / Listing / Strategy / Review / Browser Agent
  -> Artifact + Handoff + State Reducer
  -> 用户确认写操作
  -> Durable Runtime + Browser Worker
  -> 模拟店铺写入、回读验证和持久化回执
  -> 会话记忆、商品账本、时间线和 Trace
```

严格 DAG 管理阶段依赖；ReAct Loop 只在 Agent 内做有预算的工具探索。所有写操作仍经过权限、审批、幂等、租约和回读验证。

## v50 新增的发布真实性协议

`GET /api/release/final` 返回四类独立证据：

1. `offline_core`：全量 pytest 与确定性业务回归。
2. `real_browser`：Playwright Chromium 打开四个页面并生成截图。
3. `real_deepseek`：只有真实 DeepSeek 完成记录存在时通过。
4. `evidence_integrity`：最终证据文件 SHA-256 校验。

`interview_ready` 依赖核心回归、浏览器和证据清单；`real_external_chain_validated` 单独依赖 DeepSeek。二者分开可以在没有外部额度时诚实展示本地架构，又不会把离线结果说成真实 API 结果。

## UI 收尾

- 对话欢迎区覆盖商品上新、市场调研和历史商品销售查询。
- 模型与浏览器服务使用可用、降级、失败三种可见状态。
- 持久化写任务显示“后台任务已保存”，刷新后自动恢复。
- Runtime Job ID 只放在元素辅助信息、运维和 Trace，不占据用户结果主叙事。
- 桌面三栏、历史栏折叠和移动双视图继续沿用 v39 的布局约束。

## 最终验收

```bash
python scripts/run_v50_acceptance.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8250
ECOMPILOT_VISUAL_BASE_URL=http://127.0.0.1:8250 python scripts/run_v50_browser_check.py
python scripts/build_v50_evidence.py
```

配置密钥后增加：

```bash
python scripts/run_v50_live_smoke.py
python scripts/build_v50_evidence.py
```

## 面试边界

- Seller Center 是项目自建的模拟业务系统，不是淘宝、京东或亚马逊生产后台。
- 市场数据是本地演示数据，不能代表实时市场。
- SQLite Durable Runtime 展示队列、租约、幂等、Saga 和 Outbox 协议，不等同于跨主机高可用基础设施。
- 沙盒是本地隔离参考实现，不等同于经过安全认证的容器或微虚拟机平台。
- 真实 DeepSeek 是否通过必须以 `reports/v50/live_deepseek_smoke.json` 为准。
