# Bad Case Stories

## Story A: Select 控件 API 错误

**Problem**：早期 Playwright 对 HTML `<select>` 使用了 `fill()`。

**Symptom**：页面能够打开，但定位器在操作阶段失败，提交请求没有发生。

**Root Cause**：浏览器动作抽象只区分字段名，没有区分控件语义。

**Fix**：增加 `_select()`，内部固定调用 `select_option()`；文本字段继续使用 `_fill()`。

**Evidence**：`test_select_helper_never_uses_fill` 使用一个一旦调用 `fill()` 就失败的
Locator；Interview Case `browser_06_select_control` 也锁住行为。真实 Chromium 仍需在
有 Chromium 的环境用 `--real-browser` 复验。

**Why missed**：Mock Backend 直接写 Store，不经过 DOM 控件，因此单元业务回归无法发现。

## Story B: Python 与 JavaScript 换行转义

**Problem**：Seller Center HTML 写在 Python 三引号字符串中。JavaScript 的 `\n` 如果
没有正确双重转义，会在最终 HTML 中变成真实换行，破坏脚本语法。

**Symptom**：按钮和页面都存在，但 `submitPlan()` 无法正常执行，浏览器等待结果超时，
服务端日志也看不到执行 POST。

**Root Cause**：测试了 Python 源字符串，却没有测试最终渲染给浏览器的 HTML。

**Fix**：模板使用 `split('\\n')` 的 Python 表达，最终 HTML 保持合法 JavaScript
`split('\n')`；同时校验 Fetch body 的 Ticket 与 Plan 合同。

**Evidence**：`test_browser_bug_story_contracts_remain_fixed` 检查最终渲染产物，不检查源
代码文本。真实 Chromium 视觉和 Console 证据由 `--real-browser` 阶段生成。

## 共同结论

Mock 适合业务规则快速回归，却不能证明 DOM 和脚本正确。真实 Browser Eval 不是为了
替代 Mock，而是覆盖不同的故障层；页面返回 200 也不能替代字段级回读验证。

## Story C: 跨进程 Ticket 与旧服务误连接

**Problem**：最初的外部 Browser Eval 在评测进程签发进程内 Ticket，再让 Uvicorn 进程
消费；两个进程不共享 Ticket Store。套件又使用固定端口，端口被旧 V15 服务占用时，
仅检查 URL 可访问会误连旧版本。

**Symptom**：第一次真实请求返回 `browser execution ticket not found`；改动后表面通过，
但执行截图落入旧 `v15/data`，暴露证据来源不对。

**Fix**：真实评测通过 `/tasks/run` 和 `/seller-center/execute` 驱动服务内工作流，使审批、
Ticket 和状态位于同一进程；套件通过端口 0 获取动态空闲端口，显式设置最终版
`PYTHONPATH`，并在等待健康检查时确认自己启动的 Uvicorn 仍存活。

**Evidence**：最终 Browser Report 的动态 Base URL 为 `127.0.0.1:46171`，4/4 通过；执行、
回读、幂等和桌面/移动截图全部写入 `v15_interview/data/browser_artifacts/`。

**Why valuable**：它说明“HTTP 成功”和“测试通过”都不足够，还必须检查测试究竟驱动了
哪个进程、哪个版本以及证据最终落在哪里。
