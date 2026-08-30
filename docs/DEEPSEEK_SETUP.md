# DeepSeek Provider Setup

## 支持范围

EcomPilot V35 支持 DeepSeek OpenAI-format Chat Completions API：

```text
POST https://api.deepseek.com/chat/completions
```

支持 Market、Listing、Strategy、Review、Analytics 五个 LLM Agent。Market 与 Analytics
Agent 可在有界 ReAct 中
生成只读 SQL，经 AST 和 SQLite 双层策略后查询冻结市场数据库。系统保留调用预算、HTTP
重试、本地 Schema 校验、Trace、Token 和费用估算。毛利、库存、审批和 Browser 权限仍由
确定性模块控制。

V35 对 ReAct 的多轮工具调用显式设置 `thinking.type=disabled`。这是因为 DeepSeek V4
思考模式的工具调用要求下一轮回传 `reasoning_content`，而当前项目只保存标准化的
assistant/tool 消息。关闭工具调用阶段的思考模式不影响模型自主选择工具，并能避免第二轮
因缺少思考上下文而失败；普通结构化文案生成仍使用 DeepSeek 的标准 Chat Completions。

官方参考：

- https://api-docs.deepseek.com/api/create-chat-completion
- https://api-docs.deepseek.com/guides/json_mode
- https://api-docs.deepseek.com/quick_start/pricing

## 临时安全配置

不要把 Key 写入仓库。当前 Shell 中使用隐藏输入：

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v35
read -rsp "DeepSeek API Key: " DEEPSEEK_API_KEY
echo
export DEEPSEEK_API_KEY
export ECOMPILOT_LLM_PROVIDER=deepseek
export ECOMPILOT_LLM_MODEL=deepseek-v4-pro
export ECOMPILOT_LLM_BASE_URL=https://api.deepseek.com
export ECOMPILOT_LLM_AGENTS=market_agent,listing_agent,strategy_agent,review_agent,analytics_agent
export ECOMPILOT_REACT_AGENTS=market_agent,strategy_agent,analytics_agent
export ECOMPILOT_LLM_FALLBACK=fail_closed
export ECOMPILOT_LLM_TIMEOUT_SECONDS=90
export ECOMPILOT_LLM_MAX_RETRIES=1
export ECOMPILOT_LLM_MAX_OUTPUT_TOKENS=3000
export ECOMPILOT_NODE_TIMEOUT_SECONDS=190
export ECOMPILOT_LLM_MAX_CALLS_PER_AGENT=7
export ECOMPILOT_STRATEGY_CANDIDATES=auto
export ECOMPILOT_REACT_MAX_STEPS=5
export ECOMPILOT_REACT_MAX_TOOL_CALLS=8
```

可使用 `ECOMPILOT_LLM_API_KEY` 作为 Provider-neutral Key 变量，但项目不会在 DeepSeek
模式下误用 `OPENAI_API_KEY`。

## 验证顺序

```bash
python scripts/run_llm_preflight.py
python scripts/run_real_llm_smoke.py
```

预检应显示：

```text
provider=deepseek
model=deepseek-v4-pro
base_url=https://api.deepseek.com
api_key_configured=true
ready=true
```

Smoke 应至少产生五个完成的 `model_records`：Market ReAct 至少两次，Listing、Strategy、
Review 各至少一次。Market 输出还应包含：

```text
research_mode=react_text_to_sql
sql_research.policy.status=allowed
sql_research.policy.read_only_connection=true
```

模型记录应包含：

```text
provider=deepseek
usage_source=actual
structured_validation=passed
structured_output_mode=json_object_local_schema
finish_reason=stop
```

`ECOMPILOT_NODE_TIMEOUT_SECONDS` 必须大于一轮 LLM 请求及其全部 HTTP 重试的总预算。
预检会输出 `llm_request_budget_seconds` 和 `node_timeout_seconds`；若节点预算过小，
会以 `node_timeout_below_llm_request_budget` 拒绝启动，避免慢响应被错误重跑。

确认 Smoke 后再运行付费的 20 Case 对照：

```bash
python scripts/run_interview_suite.py --live-llm --real-browser
```

也可只运行模型对照：

```bash
python scripts/run_llm_comparison.py \
  --live-llm \
  --provider deepseek \
  --model deepseek-v4-flash \
  --base-url https://api.deepseek.com
```

## 启动真实用户联动页面

在完成上面的 DeepSeek 配置后，补充真实浏览器配置并使用严格启动器：

```bash
export ECOMPILOT_BROWSER_BACKEND=playwright
export ECOMPILOT_BROWSER_BASE_URL=http://127.0.0.1:8131
python -m playwright install chromium
python scripts/run_linked_service.py
```

不要用普通 `uvicorn app.main:app` 作为真实用户演示启动命令。严格启动器会在 DeepSeek、
Market/Strategy/Analytics ReAct、五个 LLM Agent、`fail_closed`、Playwright 或 Chromium 任一项未就绪时拒绝启动。启动成功后访问
`http://127.0.0.1:8131/`，并在第二个终端执行：

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v35
python scripts/run_live_linked_ui_check.py
```

只有输出同时包含 `"passed": true`、`"runtime_status_stubbed": false`、至少五条模型调用和
`"browser_backend": "playwright"`，才说明真实用户链路验收通过。

## 与 OpenAI 的差异

DeepSeek JSON Output 保证合法 JSON，但不等同于 OpenAI Responses 的 Provider-side strict
JSON Schema。EcomPilot 会把完整 Schema 加入 Prompt，再用 Pydantic 检查字段、类型、长度
和额外字段。失败时最多进行一次 JSON 修复；仍失败则按 `fail_closed` 终止。

## 常见错误

- `missing_api_key`：Key 没有导出到启动进程所在 Shell。
- `401/403`：Key、账户权限或余额异常。
- `429`：速率限制，程序只做有界重试。
- `finish_reason=length`：输出被截断，系统按 Incomplete 失败处理。
- `node_timeout_below_llm_request_budget`：提高 `ECOMPILOT_NODE_TIMEOUT_SECONDS`，或降低
  单次请求超时/重试次数。已开始执行的超时节点不会自动重跑，以免产生重复调用或副作用。
- `content was empty`：DeepSeek 返回空内容，系统不会把它当成功。
- `model_not_found`：检查 DeepSeek 当前模型名称和账户可用范围。
