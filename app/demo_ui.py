from __future__ import annotations


DEMO_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EcomPilot 运维监控台</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #172033;
      --muted: #667085;
      --line: #d8dee8;
      --soft: #f5f7fb;
      --panel: #ffffff;
      --accent: #0f766e;
      --accent-2: #2563eb;
      --warn: #b45309;
      --bad: #b42318;
      --ok: #047857;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #eef2f7;
    }
    header {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: center;
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 { margin: 0; font-size: 20px; line-height: 1.2; letter-spacing: 0; }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      max-width: 1480px;
      margin: 0 auto;
    }
    section, aside {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }
    .left { display: flex; flex-direction: column; gap: 16px; }
    .block { padding: 16px; }
    .block + .block { border-top: 1px solid var(--line); }
    h2 { margin: 0 0 12px; font-size: 15px; letter-spacing: 0; }
    label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 8px; }
    textarea {
      width: 100%;
      min-height: 168px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      font: inherit;
      line-height: 1.45;
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-top: 12px;
    }
    button {
      min-height: 36px;
      padding: 0 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
      white-space: nowrap;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: white; }
    button.blue { background: var(--accent-2); border-color: var(--accent-2); color: white; }
    button:disabled { opacity: .55; cursor: wait; }
    .nav-link {
      display: inline-flex;
      align-items: center;
      min-height: 36px;
      padding: 0 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      text-decoration: none;
      white-space: nowrap;
    }
    .readonly-banner {
      padding: 10px 12px;
      border: 1px solid #b9ddd7;
      border-radius: 6px;
      background: #eefaf8;
      color: var(--accent);
      font-size: 13px;
      line-height: 1.5;
    }
    .readonly-field {
      min-height: 120px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--soft);
      color: var(--ink);
      line-height: 1.5;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .checkline { display: flex; gap: 8px; align-items: center; color: var(--muted); font-size: 13px; }
    input[type="checkbox"] { width: 16px; height: 16px; }
    .statusbar {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    .stat {
      padding: 12px 14px;
      border-right: 1px solid var(--line);
      min-height: 70px;
    }
    .stat:last-child { border-right: 0; }
    .stat small { display: block; color: var(--muted); margin-bottom: 6px; }
    .stat strong { display: block; font-size: 18px; overflow-wrap: anywhere; }
    .tabs {
      display: flex;
      gap: 0;
      border-bottom: 1px solid var(--line);
      overflow-x: auto;
    }
    .tab {
      border: 0;
      border-right: 1px solid var(--line);
      border-radius: 0;
      background: #fbfcfe;
      min-width: 112px;
    }
    .tab.active { background: white; color: var(--accent); box-shadow: inset 0 -2px 0 var(--accent); }
    .view { display: none; padding: 16px; }
    .view.active { display: block; }
    .grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfe;
      min-height: 88px;
      min-width: 0;
      overflow: hidden;
    }
    .item h3 { margin: 0 0 8px; font-size: 14px; letter-spacing: 0; }
    .item p, .item li { color: var(--muted); font-size: 13px; line-height: 1.45; }
    .item p { margin: 0; overflow-wrap: anywhere; }
    ul { margin: 0; padding-left: 18px; }
    pre {
      margin: 0;
      min-height: 260px;
      max-height: 620px;
      overflow: auto;
      background: #101828;
      color: #e5e7eb;
      padding: 14px;
      border-radius: 8px;
      font-size: 12px;
      line-height: 1.45;
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      margin: 0 6px 6px 0;
      background: white;
    }
    .ok { color: var(--ok); }
    .bad { color: var(--bad); }
    .warn { color: var(--warn); }
    .a2a-strip {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      margin-bottom: 16px;
    }
    .a2a-metric { padding: 11px 12px; border-right: 1px solid var(--line); background: #fbfcfe; }
    .a2a-metric:last-child { border-right: 0; }
    .a2a-metric small { display: block; color: var(--muted); margin-bottom: 4px; }
    .a2a-metric strong { font-size: 15px; overflow-wrap: anywhere; }
    .tech-section + .tech-section { margin-top: 20px; }
    .tech-section h2 { margin-bottom: 8px; }
    .table-wrap { width: 100%; overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
    table { width: 100%; border-collapse: collapse; min-width: 780px; font-size: 12px; }
    th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); background: #f8fafc; font-weight: 600; }
    tbody tr:last-child td { border-bottom: 0; }
    td code { font-size: 11px; color: #344054; overflow-wrap: anywhere; }
    .lineage-list { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
    .lineage-row {
      display: grid;
      grid-template-columns: minmax(130px, .8fr) minmax(180px, 1fr) minmax(0, 2fr);
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      align-items: start;
      font-size: 12px;
    }
    .lineage-row:last-child { border-bottom: 0; }
    .lineage-row small { display: block; color: var(--muted); margin-bottom: 3px; }
    .lineage-flow { color: var(--muted); overflow-wrap: anywhere; }
    @media (max-width: 980px) {
      header { grid-template-columns: 1fr; position: static; }
      main { grid-template-columns: minmax(0, 1fr); width: 100%; max-width: 100vw; overflow: hidden; }
      .left, section, aside, .view, .grid2, .item { min-width: 0; max-width: 100%; }
      .tabs { width: 100%; max-width: 100%; }
      .statusbar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid2 { grid-template-columns: 1fr; }
      .a2a-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .a2a-metric { border-bottom: 1px solid var(--line); }
      .lineage-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>EcomPilot 运维监控台（只读）</h1>
    <div class="toolbar" style="margin:0">
      <a class="nav-link" href="/">用户工作台</a>
      <a class="nav-link" href="/traces">运行 Trace</a>
    </div>
  </header>
  <main>
    <div class="left">
      <aside>
        <div class="block">
          <h2>用户任务快照</h2>
          <div class="readonly-banner">该页面只观察用户工作台产生的任务，不创建、审批、恢复或执行任何业务操作。</div>
          <label for="goalView" style="margin-top:12px">运营目标</label>
          <div class="readonly-field" id="goalView">等待用户提交任务。</div>
          <div class="toolbar">
            <span class="pill" id="runtime">monitoring</span>
            <span class="pill" id="llmRuntime">LLM: loading</span>
            <span class="pill" id="browserRuntime">Browser: loading</span>
          </div>
        </div>
      </aside>
      <aside>
        <div class="block">
          <h2>模拟店铺状态快照</h2>
          <pre id="sellerCenter">{}</pre>
        </div>
      </aside>
    </div>
    <section>
      <div class="statusbar">
        <div class="stat"><small>任务状态</small><strong id="taskStatus">-</strong></div>
        <div class="stat"><small>市场样本</small><strong id="marketSamples">-</strong></div>
        <div class="stat"><small>毛利率</small><strong id="marginRate">-</strong></div>
        <div class="stat"><small>执行验证</small><strong id="verification">-</strong></div>
        <div class="stat"><small>本次运行模型调用</small><strong id="runModelCalls">-</strong></div>
        <div class="stat"><small>任务累计模型调用</small><strong id="modelCalls">-</strong></div>
      </div>
      <div class="tabs">
        <button class="tab active" onclick="showTab('summary', this)">总览</button>
        <button class="tab" onclick="showTab('market', this)">Market</button>
        <button class="tab" onclick="showTab('listing', this)">Listing</button>
        <button class="tab" onclick="showTab('strategy', this)">Strategy</button>
        <button class="tab" onclick="showTab('analytics', this)">Analytics</button>
        <button class="tab" onclick="showTab('review', this)">Review</button>
        <button class="tab" onclick="showTab('routing', this)">Routing</button>
        <button class="tab" onclick="showTab('memory', this); loadMemory(lastState)">Memory</button>
        <button class="tab" onclick="showTab('context', this); loadContext(lastState?.conversation_id)">Context</button>
        <button class="tab" onclick="showTab('sandbox', this)">Sandbox</button>
        <button class="tab" onclick="showTab('access', this)">Access</button>
        <button class="tab" onclick="showTab('execution', this)">Execution</button>
        <button class="tab" onclick="showTab('reliability', this); loadReliability(lastState?.task_id)">Reliability</button>
        <button class="tab" onclick="showTab('concurrency', this); loadRuntime()">Concurrency</button>
        <button class="tab" onclick="showTab('resilience', this); loadOperationalReadiness()">Resilience</button>
        <button class="tab" onclick="showTab('a2a', this)">A2A 协作</button>
        <button class="tab" onclick="showTab('release', this); loadRelease()">Release</button>
        <button class="tab" onclick="showTab('raw', this)">Raw</button>
      </div>
      <div id="summary" class="view active"><div class="grid2" id="summaryGrid"></div></div>
      <div id="market" class="view"><div class="grid2" id="marketGrid"></div></div>
      <div id="listing" class="view"><div class="grid2" id="listingGrid"></div></div>
      <div id="strategy" class="view"><div class="grid2" id="strategyGrid"></div></div>
      <div id="analytics" class="view"><div class="grid2" id="analyticsGrid"></div></div>
      <div id="review" class="view"><div class="grid2" id="reviewGrid"></div></div>
      <div id="routing" class="view"><div class="grid2" id="routingGrid"></div></div>
      <div id="memory" class="view"><div class="grid2" id="memoryGrid"></div></div>
      <div id="context" class="view">
        <div class="a2a-strip" id="contextStats"></div>
        <div class="grid2" id="contextGrid"></div>
      </div>
      <div id="sandbox" class="view"><div class="grid2" id="sandboxGrid"></div></div>
      <div id="access" class="view">
        <div class="a2a-strip" id="accessStats"></div>
        <div class="grid2" id="accessGrid"></div>
        <div class="tech-section">
          <h2>访问控制决策</h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>决定</th><th>用户</th><th>租户</th><th>角色</th><th>动作</th><th>资源租户</th><th>原因</th></tr></thead>
              <tbody id="accessRows"></tbody>
            </table>
          </div>
        </div>
      </div>
      <div id="execution" class="view">
        <div class="a2a-strip" id="executionStats"></div>
        <div class="grid2" id="executionGrid"></div>
      </div>
      <div id="reliability" class="view">
        <div class="a2a-strip" id="reliabilityStats"></div>
        <div class="grid2" id="reliabilityGrid"></div>
      </div>
      <div id="concurrency" class="view">
        <div class="a2a-strip" id="concurrencyStats"></div>
        <div class="grid2" id="concurrencyGrid"></div>
      </div>
      <div id="resilience" class="view">
        <div class="a2a-strip" id="resilienceStats"></div>
        <div class="grid2" id="resilienceGrid"></div>
      </div>
      <div id="a2a" class="view">
        <div class="a2a-strip" id="a2aStats"></div>
        <div class="tech-section">
          <h2>能力目录与权限</h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Capability</th><th>负责 Agent</th><th>输入 Artifact</th><th>输出 Artifact</th><th>允许工具</th><th>模式</th></tr></thead>
              <tbody id="capabilityRows"></tbody>
            </table>
          </div>
        </div>
        <div class="tech-section">
          <h2>委派与重试链</h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>状态</th><th>Capability</th><th>发送至</th><th>尝试</th><th>输入引用</th><th>输出引用</th><th>父委派</th><th>耗时</th></tr></thead>
              <tbody id="delegationRows"></tbody>
            </table>
          </div>
        </div>
        <div class="tech-section">
          <h2>能力票据与安全账本</h2>
          <div class="a2a-strip" id="securityStats"></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>事件</th><th>决定</th><th>Agent</th><th>Capability</th><th>工具</th><th>票据</th><th>使用次数</th><th>原因</th></tr></thead>
              <tbody id="securityRows"></tbody>
            </table>
          </div>
        </div>
        <div class="tech-section">
          <h2>Artifact 数据血缘</h2>
          <div class="lineage-list" id="artifactLineage"></div>
        </div>
      </div>
      <div id="release" class="view">
        <div class="a2a-strip" id="releaseStats"></div>
        <div class="grid2" id="releaseGrid"></div>
        <div class="tech-section">
          <h2>威胁与控制矩阵</h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>ID</th><th>风险</th><th>控制层</th><th>测试证据</th><th>边界</th></tr></thead>
              <tbody id="threatRows"></tbody>
            </table>
          </div>
        </div>
        <div class="tech-section">
          <h2>证据文件摘要</h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>类型</th><th>路径</th><th>SHA-256</th><th>大小</th></tr></thead>
              <tbody id="evidenceRows"></tbody>
            </table>
          </div>
        </div>
      </div>
      <div id="raw" class="view"><pre id="rawJson">{}</pre></div>
    </section>
  </main>
  <script>
    let lastState = null;
    const pageParams = new URLSearchParams(location.search);
    const requestedTaskId = pageParams.get('task_id');
    const pinnedTask = pageParams.get('pin') === '1';
    let watchedTaskId = requestedTaskId || '';
    let lastRenderedVersion = -1;
    let polling = false;
    let capabilityCatalog = null;

    function showTab(id, button) {
      document.querySelectorAll('.tab').forEach(btn => btn.classList.remove('active'));
      document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
      button.classList.add('active');
      document.getElementById(id).classList.add('active');
    }

    async function loadSellerCenter() {
      const res = await fetch('/seller-center/state');
      document.getElementById('sellerCenter').textContent = JSON.stringify(await res.json(), null, 2);
    }

    function renderState(state) {
      watchedTaskId = state.task_id;
      lastRenderedVersion = state.checkpoint_version;
      const pinQuery = pinnedTask ? '&pin=1' : '';
      history.replaceState(null, '', `/ops?task_id=${encodeURIComponent(state.task_id)}${pinQuery}`);
      document.getElementById('goalView').textContent = state.goal;
      const market = state.agent_outputs.market_agent || {};
      const priceGate = state.agent_outputs.market_price_gate_agent || {};
      const listing = state.agent_outputs.listing_agent || {};
      const strategy = state.agent_outputs.strategy_agent || {};
      const analytics = state.agent_outputs.analytics_agent || {};
      const review = state.agent_outputs.review_agent || {};
      const browser = state.agent_outputs.browser_agent || {};
      const margin = strategy.margin || {};
      const verify = browser.verification || {};
      const strategyStage = state.context_usage?.['strategy_agent:stage'] || {};
      const sourceContextTokens = Number(strategyStage.source_context_tokens || 0);
      const stageContextTokens = Number(strategyStage.stage_context_tokens || 0);
      const contextReduction = sourceContextTokens > 0
        ? `${Math.round((1 - stageContextTokens / sourceContextTokens) * 10000) / 100}%`
        : '未记录';
      const revisionLoop = state.workflow_loops?.compliance_repair || state.workflow_loops?.listing_review || null;
      const taskStatus = document.getElementById('taskStatus');
      const outcome = state.presentation?.outcome || state.outcome || state.status;
      taskStatus.textContent = taskStatusLabel(outcome);
      taskStatus.title = outcome;
      document.getElementById('marketSamples').textContent = `${market.sample_size?.competitors || 0}/${market.sample_size?.reviews || 0}`;
      document.getElementById('marginRate').textContent = margin.margin_rate == null ? '-' : `${Math.round(margin.margin_rate * 10000) / 100}%`;
      document.getElementById('verification').textContent = verify.verified
        ? 'passed'
        : state.status === 'waiting_for_approval'
          ? '等待用户确认'
          : browser.risk
            ? 'blocked/failed'
            : '-';
      document.getElementById('modelCalls').textContent = state.model_records.length;
      loadRunModelCalls(state.run_id);
      document.getElementById('summaryGrid').innerHTML = [
        item('约束', kv(state.constraints)),
        item('节点', nodeList(state.nodes)),
        item('工具调用', state.tool_records.map(r => `${r.tool_name}: ${r.status}`).join('<br>')),
        item('上下文', Object.entries(state.context_usage).map(([k,v]) => `${k}: ${v.token_estimate}`).join('<br>')),
        item('模型降级', (state.model_fallbacks || []).length ? kv(state.model_fallbacks) : '<span class="ok">none</span>'),
        item('统一失败协议', state.presentation?.failure ? kv(state.presentation.failure) : '<span class="ok">none</span>'),
        item('可选能力降级', (state.presentation?.degradations || []).length ? kv(state.presentation.degradations) : '<span class="ok">none</span>'),
        item('v65 精简核心', kv({
          strategy_protocol: strategy.core_protocol_version || 'not_observed',
          optional_evidence_tools: `${(strategy.selected_evidence_tools || []).length}/1`,
          strategy_context_reduction: contextReduction,
          recoverable_degradations: (state.degradations || []).filter(item => item.recoverable).length,
          total_degradations: (state.degradations || []).length
        })),
        item('受控合规返工', revisionLoop ? kv({
          phase: revisionLoop.phase,
          iteration: revisionLoop.iteration,
          max_iterations: revisionLoop.max_iterations,
          target_agents: revisionLoop.target_agents,
          completed_agents: revisionLoop.completed_agents,
          safe_finalize: revisionLoop.safe_finalize,
          stop_reason: revisionLoop.stop_reason,
          source_artifact_ref: revisionLoop.source_artifact_ref,
          revised_artifact_refs: revisionLoop.revised_artifact_refs
        }) : '<span class="ok">未触发</span>'),
        item('浏览器执行', kv({ backend: browser.browser_result?.backend, actions: browser.browser_result?.actions?.length, screenshot: browser.browser_result?.screenshot_path })),
        item('恢复', kv({ run_id: state.run_id, parent_run_id: state.parent_run_id, resume_count: state.resume_count, checkpoint_version: state.checkpoint_version }))
      ].join('');
      const marketLayers = market.market_layers || {};
      const marketStats = market.market_statistics || {};
      const coreLayer = marketLayers.core_comparable || {};
      const adjacentLayer = marketLayers.adjacent_tier || {};
      const fullLayer = marketLayers.full_valid_market || {};
      const cleaningDecisions = marketStats.decisions || [];
      const excludedDecisions = cleaningDecisions.filter(decision => decision.excluded);
      const flaggedDecisions = cleaningDecisions.filter(
        decision => !decision.excluded && (decision.statistical_flags || []).length
      );
      const layerDecisions = marketLayers.decisions || [];
      document.getElementById('marketGrid').innerHTML = [
        item('价格门控结论', Object.keys(priceGate).length ? kv({
          status: priceGate.status,
          position: priceGate.position,
          reason_code: priceGate.reason_code,
          target_price: priceGate.target_price,
          core_reference_price: priceGate.core_reference_price,
          deviation_rate: priceGate.deviation_rate,
          acceptance_band: priceGate.acceptance_band,
          suggested_price_range: priceGate.suggested_price_range,
          evidence_quality: priceGate.evidence_quality
        }) : '<span class="warn">价格检查尚未执行</span>'),
        item('核心可比层（用于价格决策）', layerEvidence(coreLayer, {
          reference_price: marketLayers.core_reference_price,
          reference_method: marketLayers.reference_method,
          distribution_status: marketLayers.distribution_status
        })),
        item('相邻档次层（只作解释）', layerEvidence(adjacentLayer)),
        item('全市场层（只作范围展示）', layerEvidence(fullLayer)),
        item('样本清洗摘要', kv({
          input_count: marketStats.input_count,
          retained_count: marketStats.retained_count,
          excluded_count: marketStats.excluded_count,
          retained_ratio: marketStats.retained_ratio,
          mode: marketStats.mode,
          warnings: marketStats.warnings,
          content_hash: marketStats.content_hash
        })),
        item('被排除的脏样本', excludedDecisions.length
          ? decisionAudit(excludedDecisions)
          : '<span class="ok">没有样本被排除</span>'),
        item('保留的极端但可解释样本', flaggedDecisions.length
          ? decisionAudit(flaggedDecisions)
          : '<span class="ok">没有需特别解释的保留样本</span>'),
        item('可比层分配审计', layerDecisions.length
          ? layerAudit(layerDecisions)
          : '<span class="warn">分层尚未执行</span>'),
        item('高频卖点', pills(market.top_features || [])),
        item('用户痛点', pills(market.pain_points || [])),
        item('关键词', pills(market.keywords || [])),
        item('研究模式', escapeHtml(market.research_mode || 'deterministic')),
        item('Text-to-SQL 证据', market.sql_research ? kv({
          query_id: market.sql_research.query_id,
          sql: market.sql_research.normalized_sql,
          rows: market.sql_research.row_count,
          policy: market.sql_research.policy?.status,
          read_only: market.sql_research.policy?.read_only_connection,
          insight: market.sql_research.insight_summary
        }) : '<span class="warn">本次未启用 Market ReAct</span>')
      ].join('');
      document.getElementById('listingGrid').innerHTML = [
        item('标题', escapeHtml(listing.title || '-')),
        item('关键词', pills(listing.keywords || [])),
        item('卖点', list(listing.bullets || [])),
        item('合规说明', list(listing.compliance_notes || [])),
        item('语义修正审计', (listing.semantic_corrections || []).length
          ? `<pre>${escapeHtml(JSON.stringify(listing.semantic_corrections, null, 2))}</pre>`
          : '<span class="ok">无需修正</span>'),
        item('修订信息', listing.revision_iteration
          ? kv({ iteration: listing.revision_iteration, applied_findings: listing.revision_applied_findings })
          : '<span class="ok">初始版本</span>')
      ].join('');
      document.getElementById('strategyGrid').innerHTML = [
        item('促销方案', escapeHtml(strategy.launch_plan || '-')),
        item('价格', kv({ price: strategy.price, coupon: strategy.coupon, planned_units: strategy.planned_units })),
        item('毛利', kv(strategy.margin || {})),
        item('库存', kv(strategy.inventory_check || {})),
        item('自主选择的策略证据', (strategy.selected_evidence_tools || []).length
          ? pills(strategy.selected_evidence_tools)
          : '<span class="warn">本次未调用可选策略证据</span>'),
        item('策略证据结果', `<pre>${escapeHtml(JSON.stringify(strategy.decision_evidence || {}, null, 2))}</pre>`),
        item('单方案数字审计', `<pre>${escapeHtml(JSON.stringify(strategy.proposal_audit || {}, null, 2))}</pre>`),
        item('数字所有权', strategy.strategy_render_version
          ? `<pre>${escapeHtml(JSON.stringify(strategy.numeric_ownership || {}, null, 2))}</pre>`
          : '<span class="warn">旧版策略未声明数字来源</span>'),
        item('确定性渲染清单', strategy.strategy_render_version
          ? kv({ version: strategy.strategy_render_version, input_hash: strategy.render_manifest?.input_hash, output_hash: strategy.render_manifest?.output_hash })
          : '<span class="warn">尚未执行确定性渲染</span>'),
        item('语义修正审计', (strategy.semantic_corrections || []).length
          ? `<pre>${escapeHtml(JSON.stringify(strategy.semantic_corrections, null, 2))}</pre>`
          : '<span class="ok">无需修正</span>'),
        item('修订信息', strategy.revision_iteration
          ? kv({ iteration: strategy.revision_iteration, applied_findings: strategy.revision_applied_findings })
          : '<span class="ok">初始版本</span>')
      ].join('');
      const analyticsMetrics = analytics.sales?.metrics || {};
      const comparison = analytics.comparison || {};
      const campaigns = analytics.campaigns || {};
      const inventory = analytics.inventory || {};
      document.getElementById('analyticsGrid').innerHTML = analytics.product_id ? [
        item('查询对象与周期', kv({
          product_id: analytics.product_id,
          period: analytics.period?.label,
          start_date: analytics.period?.start_date,
          end_date: analytics.period?.end_date
        })),
        item('权威销售指标', kv({
          units_sold: analyticsMetrics.units_sold,
          revenue: analyticsMetrics.revenue,
          orders: analyticsMetrics.orders,
          conversion_rate: analyticsMetrics.conversion_rate,
          ending_inventory: analyticsMetrics.ending_inventory
        })),
        item('Agent 结论', escapeHtml(analytics.narrative || '-')),
        item('自主选择的只读工具', pills(analytics.selected_evidence_tools || [])),
        item('周期对比', comparison.change ? kv(comparison.change) : '<span class="ok">本次问题未要求周期对比</span>'),
        item('活动表现', campaigns.summary ? kv(campaigns.summary) : '<span class="ok">本次问题未要求活动分析</span>'),
        item('库存历史', inventory.product_id ? kv({
          starting_inventory: inventory.starting_inventory,
          ending_inventory: inventory.ending_inventory,
          net_change: inventory.net_change,
          movement_count: inventory.movements?.length || 0
        }) : '<span class="ok">本次问题未要求库存历史</span>'),
        item('数据可信边界', kv({
          source_type: analytics.source_type,
          source_updated_at: analytics.source_updated_at,
          generation_mode: analytics.generation_mode
        }))
      ].join('') : item('销售分析', '<span class="warn">当前任务未执行 Analytics Agent</span>');
      document.getElementById('reviewGrid').innerHTML = [
        item('审核状态', review.approved_for_execution ? '<span class="ok">approved</span>' : '<span class="bad">blocked</span>'),
        item('违规项', (review.violations || []).length ? pills(review.violations) : '<span class="ok">none</span>'),
        item('模型审核项', (review.review_findings || []).length
          ? `<pre>${escapeHtml(JSON.stringify(review.review_findings, null, 2))}</pre>`
          : '<span class="warn">none</span>'),
        item('语义一致性检查', (review.consistency_checks || []).length
          ? `<pre>${escapeHtml(JSON.stringify(review.consistency_checks, null, 2))}</pre>`
          : '<span class="warn">尚未执行</span>'),
        item('修正审计汇总', (review.correction_audit || []).length
          ? `<pre>${escapeHtml(JSON.stringify(review.correction_audit, null, 2))}</pre>`
          : '<span class="ok">本次无需修正</span>'),
        item('执行来源与载荷哈希', review.execution_manifest?.payload_hash
          ? `<pre>${escapeHtml(JSON.stringify(review.execution_manifest, null, 2))}</pre>`
          : '<span class="warn">尚无执行清单</span>'),
        item('执行计划', kv(review.execution_plan || {})),
        item('执行验证', verify.verified ? kv(verify.checks || {}) : kv(verify))
      ].join('');
      const routePlan = state.route_plan || {};
      const actualAgents = Object.values(state.nodes || {})
        .filter(node => node.status !== 'skipped')
        .map(node => `${node.agent_name}: ${node.status}`);
      const capabilityTiers = Object.values(state.a2a_delegations || {}).map(record => ({
        agent: record.request?.receiver_agent,
        access: record.request?.capability_access,
        risk_scope: record.request?.risk_scope,
        approval_granted: record.request?.approval_granted,
        conversation_id: record.request?.conversation_id,
        turn_id: record.request?.turn_id
      }));
      document.getElementById('routingGrid').innerHTML = routePlan.route_id ? [
        item('版本化 Route Plan', kv({
          route_plan_version: routePlan.route_plan_version,
          route_id: routePlan.route_id,
          template_id: routePlan.template_id,
          intent: routePlan.intent,
          risk_scope: routePlan.risk_scope
        })),
        item('本轮实际 Agent', actualAgents.length ? list(actualAgents) : '<span class="ok">未调用专业 Agent</span>'),
        item('明确跳过的 Agent', routePlan.skipped_agents?.length ? pills(routePlan.skipped_agents) : '<span class="ok">none</span>'),
        item('允许的能力范围', routePlan.capability_scopes?.length ? pills(routePlan.capability_scopes) : '<span class="ok">none</span>'),
        item('意图单元', routePlan.intent_units?.length
          ? `<pre>${escapeHtml(JSON.stringify(routePlan.intent_units, null, 2))}</pre>`
          : '<span class="ok">单意图兼容路径</span>'),
        item('执行分组', routePlan.execution_groups?.length
          ? `<pre>${escapeHtml(JSON.stringify(routePlan.execution_groups, null, 2))}</pre>`
          : '<span class="warn">尚未生成执行分组</span>'),
        item('停止条件', list(routePlan.stop_conditions || [])),
        item('A2A 权限令牌上下文', capabilityTiers.length
          ? `<pre>${escapeHtml(JSON.stringify(capabilityTiers, null, 2))}</pre>`
          : '<span class="ok">本轮没有 A2A 委派</span>')
      ].join('') : item('路由计划', '<span class="warn">旧任务没有 v33 Route Plan</span>');
      loadSandbox(market.sql_research?.sandbox || null);
      loadAccess(state, market.sql_research || null);
      loadExecution(browser);
      loadReliability(state.task_id);
      loadMemory(state);
      document.getElementById('rawJson').textContent = JSON.stringify(state, null, 2);
      loadA2A(state.task_id);
    }

    async function loadMemory(state) {
      if (!state) return;
      try {
        const response = await fetch('/api/copilot/memories');
        if (!response.ok) throw new Error('memory catalog unavailable');
        const memories = await response.json();
        const usage = state.context_usage || {};
        const refs = [...new Set(Object.values(state.memory_refs || {}).flat())];
        document.getElementById('memoryGrid').innerHTML = [
          item('Context Policy 2.0', `<pre>${escapeHtml(JSON.stringify(usage, null, 2))}</pre>`),
          item('本任务实际召回', refs.length ? pills(refs) : '<span class="ok">没有召回长期偏好</span>'),
          item('已确认记忆', `<pre>${escapeHtml(JSON.stringify(memories.filter(item => item.status === 'active'), null, 2))}</pre>`),
          item('候选 / 冲突 / 停用', `<pre>${escapeHtml(JSON.stringify(memories.filter(item => item.status !== 'active'), null, 2))}</pre>`)
        ].join('');
      } catch (error) {
        document.getElementById('memoryGrid').innerHTML = item('Memory 状态', `<span class="bad">${escapeHtml(error.message)}</span>`);
      }
    }

    async function loadSandbox(receipt) {
      try {
        const response = await fetch('/api/sandbox/status');
        if (!response.ok) throw new Error('sandbox status unavailable');
        const runtime = await response.json();
        const isolation = receipt?.isolation || runtime.isolation || {};
        document.getElementById('sandboxGrid').innerHTML = [
          item('执行回执', kv({
            sandbox_id: receipt?.sandbox_id,
            status: receipt?.status || '本次未执行 SQL',
            backend: runtime.backend,
            worker_pid: receipt?.worker_pid,
            exit_code: receipt?.exit_code,
            duration_ms: receipt?.duration_ms
          })),
          item('进程隔离', kv({
            separate_process: isolation.separate_process,
            isolated_python: isolation.isolated_python,
            site_packages_disabled: isolation.site_packages_disabled,
            shell_enabled: isolation.shell_enabled ?? false,
            temporary_cwd: isolation.temporary_working_directory,
            cwd_removed: isolation.working_directory_removed
          })),
          item('环境与密钥', kv({
            environment_allowlist: receipt?.isolation?.environment_allowlist || runtime.environment_allowlist,
            environment_key_count: receipt?.isolation?.environment_key_count,
            secret_environment_present: receipt?.isolation?.secret_environment_present
          })),
          item('资源预算', kv({
            applied: receipt?.isolation?.resource_limits_applied || [],
            ...runtime.limits
          })),
          item('隔离边界', kv({
            sqlite_read_only: isolation.sqlite_read_only,
            sqlite_authorizer: isolation.sqlite_authorizer,
            namespaces: runtime.isolation?.namespaces,
            seccomp: runtime.isolation?.seccomp,
            container: runtime.isolation?.container
          }))
        ].join('');
      } catch (error) {
        document.getElementById('sandboxGrid').innerHTML = item('Sandbox 状态', `<span class="bad">${escapeHtml(error.message)}</span>`);
      }
    }

    async function loadAccess(state, sqlResearch) {
      try {
        const [policyResponse, auditResponse] = await Promise.all([
          fetch('/api/access/policy'),
          fetch('/api/access/audits?limit=50')
        ]);
        if (!policyResponse.ok || !auditResponse.ok) throw new Error('access evidence unavailable');
        const policy = await policyResponse.json();
        const audits = await auditResponse.json();
        const principal = state.principal || {};
        const tenantDelegations = Object.values(state.a2a_delegations || {}).every(
          record => record.request?.tenant_id === principal.tenant_id
        );
        const tenantTools = (state.tool_records || []).every(
          record => record.tenant_id === principal.tenant_id
        );
        document.getElementById('accessStats').innerHTML = [
          a2aMetric('访问模型', policy.model || '-'),
          a2aMetric('当前租户', principal.tenant_id || '-'),
          a2aMetric('身份', principal.subject_id || '-'),
          a2aMetric('委派绑定', tenantDelegations ? '通过' : '不一致', tenantDelegations ? 'ok' : 'bad'),
          a2aMetric('工具绑定', tenantTools ? '通过' : '不一致', tenantTools ? 'ok' : 'bad')
        ].join('');
        document.getElementById('accessGrid').innerHTML = [
          item('受信任身份', kv({
            subject_id: principal.subject_id,
            tenant_id: principal.tenant_id,
            roles: principal.roles,
            authentication_method: principal.authentication_method
          })),
          item('SQL 行级过滤', sqlResearch ? kv({
            tenant_id: sqlResearch.tenant_id,
            applied: sqlResearch.policy?.row_filter_applied,
            normalized_sql: sqlResearch.normalized_sql
          }) : '<span class="warn">本次未触发 Text-to-SQL</span>'),
          item('权限模型', kv({ roles: policy.roles, tenant_rule: policy.tenant_rule })),
          item('生产边界', escapeHtml(policy.production_boundary || '-'))
        ].join('');
        document.getElementById('accessRows').innerHTML = audits.map(record => `
          <tr>
            <td class="${record.status === 'allowed' ? 'ok' : 'bad'}">${escapeHtml(record.status)}</td>
            <td>${escapeHtml(record.subject_id)}</td>
            <td><code>${escapeHtml(record.tenant_id)}</code></td>
            <td>${escapeHtml((record.roles || []).join(', '))}</td>
            <td><code>${escapeHtml(record.action)}</code></td>
            <td><code>${escapeHtml(record.resource_tenant_id || '-')}</code></td>
            <td>${escapeHtml((record.reason_codes || []).join(', '))}</td>
          </tr>`).join('');
      } catch (error) {
        document.getElementById('accessStats').innerHTML = a2aMetric('Access 状态', error.message, 'bad');
      }
    }

    async function loadExecution(browser) {
      try {
        const [statusResponse, storeResponse] = await Promise.all([
          fetch('/api/execution/status'),
          fetch('/seller-center/state')
        ]);
        if (!statusResponse.ok || !storeResponse.ok) throw new Error('execution evidence unavailable');
        const status = await statusResponse.json();
        const store = await storeResponse.json();
        const result = browser.browser_result || {};
        const verification = browser.verification || {};
        const tenantMatched = !result.tenant_id || result.tenant_id === status.tenant_id;
        document.getElementById('executionStats').innerHTML = [
          a2aMetric('执行租户', status.tenant_id || '-'),
          a2aMetric('店铺商品', status.seller_center?.product_count || 0),
          a2aMetric('店铺促销', status.seller_center?.promotion_count || 0),
          a2aMetric('执行绑定', tenantMatched ? '通过' : '不一致', tenantMatched ? 'ok' : 'bad'),
          a2aMetric('执行验证', verification.verified ? '通过' : '未执行', verification.verified ? 'ok' : 'warn')
        ].join('');
        document.getElementById('executionGrid').innerHTML = [
          item('Seller Center 分区', kv({
            tenant_id: store.tenant_id,
            storage: status.seller_center?.storage,
            products: Object.keys(store.products || {}),
            promotions: Object.keys(store.promotions || {}),
            other_tenant_ids_exposed: status.seller_center?.other_tenant_ids_exposed
          })),
          item('一次性浏览器票据', kv(status.browser_ticket || {})),
          item('幂等命名空间', kv(status.idempotency || {})),
          item('浏览器产物分区', kv(status.browser_artifacts || {})),
          item('本次执行证据', kv({
            backend: result.backend,
            tenant_id: result.tenant_id,
            ticket_purpose: result.ticket_purpose,
            idempotent_replay: result.idempotent_replay,
            screenshot_path: result.screenshot_path
          })),
          item('诚实边界', escapeHtml(status.boundary || '-'))
        ].join('');
      } catch (error) {
        document.getElementById('executionStats').innerHTML = a2aMetric('Execution 状态', error.message, 'bad');
      }
    }

    async function loadReliability(taskId) {
      if (!taskId) return;
      try {
        const [statusResponse, toolsResponse] = await Promise.all([
          fetch('/api/reliability/status?task_id=' + encodeURIComponent(taskId)),
          fetch('/api/reliability/tool-contracts')
        ]);
        if (!statusResponse.ok || !toolsResponse.ok) throw new Error('reliability evidence unavailable');
        const status = await statusResponse.json();
        const contracts = await toolsResponse.json();
        const budget = lastState?.retry_budget || {};
        const receipts = Object.values(lastState?.execution_receipts || {});
        document.getElementById('reliabilityStats').innerHTML = [
          a2aMetric('任务重试预算', `${budget.consumed || 0}/${budget.max_attempts || 0}`),
          a2aMetric('剩余额度', Math.max(0, (budget.max_attempts || 0) - (budget.consumed || 0))),
          a2aMetric('熔断器开启', (status.circuits || []).filter(item => item.state === 'open').length,
            (status.circuits || []).some(item => item.state === 'open') ? 'bad' : 'ok'),
          a2aMetric('待人工处理', status.needs_attention_count || 0,
            status.needs_attention_count ? 'warn' : 'ok'),
          a2aMetric('可恢复结果', receipts.filter(item => item.reusable).length)
        ].join('');
        document.getElementById('reliabilityGrid').innerHTML = [
          item('任务级重试决策', budget.decisions?.length
            ? `<pre>${escapeHtml(JSON.stringify(budget.decisions, null, 2))}</pre>`
            : '<span class="ok">本次未消耗重试预算</span>'),
          item('熔断状态', (status.circuits || []).length
            ? `<pre>${escapeHtml(JSON.stringify(status.circuits, null, 2))}</pre>`
            : '<span class="ok">没有故障累积</span>'),
          item('死信与人工关注', (status.dead_letters || []).length
            ? `<pre>${escapeHtml(JSON.stringify(status.dead_letters, null, 2))}</pre>`
            : '<span class="ok">没有待人工处理任务</span>'),
          item('执行回执', receipts.length
            ? `<pre>${escapeHtml(JSON.stringify(receipts, null, 2))}</pre>`
            : '<span class="warn">本任务尚无工具执行回执</span>'),
          item('工具生命周期协议', kv({
            protocol_version: contracts.protocol_version,
            declared_tools: (contracts.tools || []).length,
            writes: (contracts.tools || []).filter(item => item.operation_type === 'write').map(item => item.name),
            reconciled_writes: (contracts.tools || []).filter(item => item.reconcile_tool).map(item => item.name)
          })),
          item('当前边界', escapeHtml(status.boundary || '-'))
        ].join('');
      } catch (error) {
        document.getElementById('reliabilityStats').innerHTML = a2aMetric('Reliability 状态', error.message, 'bad');
      }
    }

    async function loadRuntime() {
      try {
        const [statusResponse, sagasResponse] = await Promise.all([
          fetch('/api/runtime/status'),
          fetch('/api/runtime/sagas?limit=30')
        ]);
        if (!statusResponse.ok || !sagasResponse.ok) throw new Error('runtime evidence unavailable');
        const status = await statusResponse.json();
        const sagas = await sagasResponse.json();
        const queued = (status.queue || []).filter(item => item.status === 'queued')
          .reduce((sum, item) => sum + item.count, 0);
        document.getElementById('concurrencyStats').innerHTML = [
          a2aMetric('队列等待', queued, queued ? 'warn' : 'ok'),
          a2aMetric('活跃租约', status.active_job_leases || 0),
          a2aMetric('确认业务效果', status.confirmed_business_effects || 0, 'ok'),
          a2aMetric('Outbox 待发布', status.pending_outbox_events || 0),
          a2aMetric('需人工处理 Saga', status.sagas?.needs_attention || 0,
            status.sagas?.needs_attention ? 'bad' : 'ok')
        ].join('');
        document.getElementById('concurrencyGrid').innerHTML = [
          item('持久化任务队列', `<pre>${escapeHtml(JSON.stringify(status.queue || [], null, 2))}</pre>`),
          item('Worker 隔离池', `<pre>${escapeHtml(JSON.stringify(status.bulkheads || {}, null, 2))}</pre>`),
          item('Saga 执行记录', sagas.length
            ? `<pre>${escapeHtml(JSON.stringify(sagas, null, 2))}</pre>`
            : '<span class="ok">当前没有破坏性操作 Saga</span>'),
          item('并发边界', '请求由持久化队列分配；租约超时可接管；旧 Worker 受 Fencing Token 阻止；业务效果与 Outbox 在同一事务确认。')
        ].join('');
      } catch (error) {
        document.getElementById('concurrencyStats').innerHTML = a2aMetric('Concurrency 状态', error.message, 'bad');
      }
    }

    async function loadOperationalReadiness() {
      try {
        const response = await fetch('/api/operations/readiness');
        if (!response.ok) throw new Error('operational readiness evidence unavailable');
        const report = await response.json();
        if (!report.chaos) {
          document.getElementById('resilienceStats').innerHTML =
            a2aMetric('验收状态', '尚未运行', 'warn');
          document.getElementById('resilienceGrid').innerHTML = item(
            '运行指引', escapeHtml(report.reason || '请先运行 v39 operational acceptance')
          );
          return;
        }
        const chaos = report.chaos || {};
        const capacity = report.capacity || {};
        const isolation = report.isolation || {};
        const slo = report.slo || {};
        document.getElementById('resilienceStats').innerHTML = [
          a2aMetric('参考实现', report.status === 'reference_validated' ? '验收通过' : '等待验收',
            report.status === 'reference_validated' ? 'ok' : 'warn'),
          a2aMetric('故障恢复', `${chaos.recovered_scenarios || 0}/${chaos.total_scenarios || 0}`,
            chaos.passed ? 'ok' : 'bad'),
          a2aMetric('峰值吞吐', `${capacity.drain_throughput_per_second || 0}/s`,
            capacity.passed ? 'ok' : 'warn'),
          a2aMetric('SLO', slo.passed ? '达标' : '告警', slo.passed ? 'ok' : 'bad'),
          a2aMetric('跨租户泄漏', isolation.cross_tenant_leaks || 0,
            isolation.cross_tenant_leaks ? 'bad' : 'ok')
        ].join('');
        const scenarios = (chaos.scenarios || []).map(scenario => ({
          fault: scenario.fault,
          attempts: scenario.attempts,
          recovered: scenario.recovered,
          terminal: scenario.terminal_class,
          control: scenario.expected_control
        }));
        document.getElementById('resilienceGrid').innerHTML = [
          item('混沌演练', `<pre>${escapeHtml(JSON.stringify(scenarios, null, 2))}</pre>`),
          item('容量基线', kv({
            jobs: capacity.jobs,
            workers: capacity.workers,
            enqueue_p95_ms: capacity.enqueue_p95_ms,
            enqueue_per_second: capacity.enqueue_throughput_per_second,
            drain_per_second: capacity.drain_throughput_per_second,
            duplicate_jobs: capacity.duplicate_jobs,
            dead_jobs: capacity.dead_jobs
          })),
          item('SLO 与告警', `<pre>${escapeHtml(JSON.stringify(slo, null, 2))}</pre>`),
          item('租户隔离审计', `<pre>${escapeHtml(JSON.stringify(isolation, null, 2))}</pre>`),
          item('生产边界', `<span class="warn">未声明真实生产基础设施就绪</span><br>${list(report.production_boundaries || [])}`)
        ].join('');
      } catch (error) {
        document.getElementById('resilienceStats').innerHTML =
          a2aMetric('Resilience 状态', error.message, 'bad');
      }
    }

    async function loadContext(conversationId) {
      if (!conversationId) return;
      try {
        const response = await fetch('/api/copilot/conversations/' + encodeURIComponent(conversationId) + '/context-status');
        if (!response.ok) throw new Error('context evidence unavailable');
        const status = await response.json();
        const trust = status.summary_trust || {};
        const budget = status.context_budget || {};
        document.getElementById('contextStats').innerHTML = [
          a2aMetric('摘要信任', trust.valid ? '有效' : '未使用', trust.valid ? 'ok' : 'warn'),
          a2aMetric('写入授权', trust.write_authority ? '允许' : '禁止', trust.write_authority ? 'bad' : 'ok'),
          a2aMetric('上下文占用', `${budget.selected_tokens || 0}/${budget.input_capacity_tokens || 0}`),
          a2aMetric('压缩', budget.compression_required ? '已触发' : '未触发', budget.compression_required ? 'warn' : 'ok'),
          a2aMetric('上下文事件', (status.events || []).length)
        ].join('');
        document.getElementById('contextGrid').innerHTML = [
          item('预算决策', kv({
            protocol: budget.protocol_version,
            window: budget.context_window_tokens,
            reserved: budget.reserved_tokens,
            input_capacity: budget.input_capacity_tokens,
            selected: budget.selected_tokens,
            reason: budget.reason
          })),
          item('保留层级', `<pre>${escapeHtml(JSON.stringify(budget.selected || [], null, 2))}</pre>`),
          item('丢弃项目', (budget.dropped_item_ids || []).length
            ? list(budget.dropped_item_ids)
            : '<span class="ok">本次没有丢弃上下文项目</span>'),
          item('摘要校验', trust.issues?.length
            ? list(trust.issues)
            : '<span class="ok">来源版本与内容哈希一致</span>'),
          item('压缩与回放事件', `<pre>${escapeHtml(JSON.stringify(status.events || [], null, 2))}</pre>`),
          item('安全边界', '摘要是可重建缓存，只用于辅助理解；售价、成本、库存、权限与审批不能由摘要单独提供。')
        ].join('');
      } catch (error) {
        document.getElementById('contextStats').innerHTML = a2aMetric('Context 状态', error.message, 'bad');
      }
    }

    async function loadA2A(taskId) {
      try {
        if (!capabilityCatalog) {
          const catalogResponse = await fetch('/api/a2a/capabilities');
          if (!catalogResponse.ok) throw new Error('capability catalog unavailable');
          capabilityCatalog = await catalogResponse.json();
        }
        const [response, securityResponse] = await Promise.all([
          fetch('/api/tasks/' + encodeURIComponent(taskId) + '/a2a'),
          fetch('/api/tasks/' + encodeURIComponent(taskId) + '/security')
        ]);
        if (!response.ok) throw new Error('A2A summary unavailable');
        if (!securityResponse.ok) throw new Error('security evidence unavailable');
        const collaboration = await response.json();
        const security = await securityResponse.json();
        if (taskId !== watchedTaskId) return;
        renderA2A(capabilityCatalog, collaboration);
        renderSecurity(security);
      } catch (error) {
        document.getElementById('a2aStats').innerHTML = a2aMetric('A2A 状态', error.message, 'bad');
      }
    }

    async function loadRelease() {
      try {
        const [readinessResponse, threatsResponse, evidenceResponse, protocolsResponse] = await Promise.all([
          fetch('/api/release/readiness'),
          fetch('/api/release/threat-model'),
          fetch('/api/release/evidence'),
          fetch('/api/release/protocols')
        ]);
        if (!readinessResponse.ok || !threatsResponse.ok || !evidenceResponse.ok || !protocolsResponse.ok) {
          throw new Error('release evidence unavailable');
        }
        const readiness = await readinessResponse.json();
        const threats = await threatsResponse.json();
        const evidence = await evidenceResponse.json();
        const protocols = await protocolsResponse.json();
        const gate = readiness.core_gate || {};
        const reliabilityGate = readiness.reliability_gate || {};
        const integrity = readiness.evidence_integrity || {};
        const integrations = readiness.external_integrations || {};
        const quality = readiness.quality_metrics || {};
        const visual = readiness.visual_gate || {};
        document.getElementById('releaseStats').innerHTML = [
          a2aMetric('发布状态', readiness.status === 'interview_ready' ? '面试最终版就绪' : '等待最终验收', readiness.status === 'interview_ready' ? 'ok' : 'warn'),
          a2aMetric('功能冻结', readiness.feature_freeze ? '是' : '否', readiness.feature_freeze ? 'ok' : 'warn'),
          a2aMetric('最终检查', `${gate.checks_passed || 0}/${gate.checks_total || 0}`, gate.passed ? 'ok' : 'warn'),
          a2aMetric('故障注入', reliabilityGate.passed ? `${reliabilityGate.scenarios_passed || 0}/${reliabilityGate.scenarios_total || 0}` : '尚未通过', reliabilityGate.passed ? 'ok' : 'warn'),
          a2aMetric('视觉验收', visual.passed ? `${visual.viewports || 0} 个视口通过` : '尚未通过', visual.passed ? 'ok' : 'warn'),
          a2aMetric('威胁覆盖', `${threats.implemented_and_tested}/${threats.controls_total}`, threats.coverage_rate === 1 ? 'ok' : 'bad'),
          a2aMetric('证据完整性', integrity.valid ? 'SHA-256 通过' : '待生成或已变化', integrity.valid ? 'ok' : 'bad')
        ].join('');
        document.getElementById('releaseGrid').innerHTML = [
          item('外部运行模式', kv({
            llm: `${integrations.llm?.mode || '-'} / ${integrations.llm?.provider || '-'}`,
            model: integrations.llm?.model,
            browser: `${integrations.browser?.mode || '-'} / ${integrations.browser?.backend || '-'}`
          })),
          item('生产边界', `<span class="warn">未声明生产就绪</span><br>${list(readiness.production_readiness?.reasons || [])}`),
          item('证据清单', kv({
            path: integrity.manifest_path,
            files: integrity.entry_count,
            changed: (integrity.changed || []).length,
            missing: (integrity.missing || []).length
          })),
          item('MVP 质量指标', kv({
            intent: quality.intent_accuracy?.value,
            entity: quality.entity_resolution_accuracy?.value,
            numeric: quality.numeric_fact_accuracy?.value,
            unsafe_writes: quality.unapproved_write_count?.value
          })),
          item('协议清单', kv({
            release: protocols.release,
            project: protocols.project_version,
            contracts: (protocols.contracts || []).length,
            run_bundle: readiness.run_bundle?.version
          })),
          item('版本定位', 'V37 在可恢复执行基础上加入受限多意图 DAG 与可验证上下文压缩；真实商业部署仍需补充跨实例协调、生产身份、执行隔离与真实店铺连接。')
        ].join('');
        document.getElementById('threatRows').innerHTML = (threats.controls || []).map(control => `
          <tr>
            <td><code>${escapeHtml(control.threat_id)}</code></td>
            <td>${escapeHtml(control.threat)}</td>
            <td>${escapeHtml((control.control_layers || []).join(' / '))}</td>
            <td>${(control.test_paths || []).map(path => `<code>${escapeHtml(path)}</code>`).join('<br>')}</td>
            <td>${escapeHtml(control.boundary)}</td>
          </tr>`).join('');
        document.getElementById('evidenceRows').innerHTML = (evidence.manifest?.entries || []).map(entry => `
          <tr>
            <td>${escapeHtml(entry.kind)}</td>
            <td><code>${escapeHtml(entry.path)}</code></td>
            <td><code title="${escapeHtml(entry.sha256)}">${escapeHtml(shortRef(entry.sha256))}</code></td>
            <td>${escapeHtml(entry.size_bytes)} B</td>
          </tr>`).join('');
      } catch (error) {
        document.getElementById('releaseStats').innerHTML = a2aMetric('Release 状态', error.message, 'bad');
      }
    }

    function renderSecurity(security) {
      const summary = security.summary || {};
      const integrity = security.integrity || {};
      document.getElementById('securityStats').innerHTML = [
        a2aMetric('账本完整性', integrity.valid ? '哈希链通过' : '校验失败', integrity.valid ? 'ok' : 'bad'),
        a2aMetric('签发', summary.issued || 0),
        a2aMetric('工具放行', summary.allowed || 0, 'ok'),
        a2aMetric('工具拒绝', summary.denied || 0, summary.denied ? 'bad' : 'ok'),
        a2aMetric('撤销', summary.revoked || 0)
      ].join('');
      document.getElementById('securityRows').innerHTML = (security.events || []).map(event => `
        <tr>
          <td>${escapeHtml(securityEventLabel(event.event_type))}</td>
          <td class="${event.decision === 'denied' ? 'bad' : 'ok'}">${escapeHtml(event.decision)}</td>
          <td>${escapeHtml(agentLabel(event.agent_name))}</td>
          <td><code>${escapeHtml(event.capability_id)}</code></td>
          <td>${escapeHtml(event.tool_name || '-')}</td>
          <td><code>${escapeHtml(shortRef(event.token_id))}</code></td>
          <td>${escapeHtml(event.use_count == null ? '-' : `${event.use_count}/${event.max_uses}`)}</td>
          <td>${escapeHtml(event.reason || '-')}</td>
        </tr>`).join('');
    }

    function securityEventLabel(value) {
      return ({token_issued:'签发', tool_allowed:'放行', tool_denied:'拒绝', token_revoked:'撤销'})[value] || value;
    }

    function renderA2A(catalog, collaboration) {
      const summary = collaboration.summary || {};
      const budget = collaboration.budget || {};
      document.getElementById('a2aStats').innerHTML = [
        a2aMetric('协议', 'A2A ' + collaboration.protocol_version),
        a2aMetric('委派', `${summary.delegation_count || 0}/${budget.max_delegations || 0}`),
        a2aMetric('状态流转', summary.transition_count || 0),
        a2aMetric('Artifact', summary.artifact_count || 0),
        a2aMetric('失败 / 重试', `${summary.failed_count || 0} / ${summary.retry_count || 0}`, summary.failed_count ? 'bad' : 'ok')
      ].join('');
      document.getElementById('capabilityRows').innerHTML = (catalog.routes || []).map(route => `
        <tr>
          <td><code>${escapeHtml(route.capability_id)}</code></td>
          <td>${escapeHtml(agentLabel(route.agent_name))}</td>
          <td>${refList(route.input_artifact_types, '无')}</td>
          <td><code>${escapeHtml(route.output_artifact_type)}</code></td>
          <td>${refList(route.allowed_tools, '无')}</td>
          <td>${route.read_only ? '<span class="ok">只读</span>' : '<span class="warn">可写</span>'}</td>
        </tr>`).join('');
      document.getElementById('delegationRows').innerHTML = (collaboration.delegations || []).map(record => `
        <tr>
          <td class="${statusClass(record.status)}">${escapeHtml(statusLabel(record.status))}</td>
          <td><code>${escapeHtml(record.capability_id)}</code></td>
          <td>${escapeHtml(agentLabel(record.receiver_agent))}</td>
          <td>${escapeHtml(record.attempt)}</td>
          <td>${refList(record.input_artifact_refs, '无')}</td>
          <td>${refList(record.output_artifact_ref ? [record.output_artifact_ref] : [], '无')}</td>
          <td>${refList(record.parent_delegation_id ? [record.parent_delegation_id] : [], '无')}</td>
          <td>${escapeHtml(record.duration_ms)} ms</td>
        </tr>`).join('');
      const artifacts = collaboration.artifacts || [];
      document.getElementById('artifactLineage').innerHTML = artifacts.length ? artifacts.map(artifact => `
        <div class="lineage-row">
          <div><small>类型</small><strong>${escapeHtml(artifact.artifact_type)}</strong></div>
          <div><small>产出者</small>${escapeHtml(agentLabel(artifact.producer))}<br><code title="${escapeHtml(artifact.artifact_id)}">${escapeHtml(shortRef(artifact.artifact_id))}</code></div>
          <div class="lineage-flow"><small>上游 Artifact → 当前 Artifact → 下游委派</small>${refList(artifact.parent_artifact_refs, '起点')} → <code>${escapeHtml(shortRef(artifact.artifact_id))}</code> → ${refList(artifact.consumer_delegation_ids, '终点')}</div>
        </div>`).join('') : '<div class="lineage-row">暂无 Artifact</div>';
    }

    function a2aMetric(label, value, className = '') {
      return `<div class="a2a-metric"><small>${escapeHtml(label)}</small><strong class="${className}">${escapeHtml(value)}</strong></div>`;
    }
    function refList(values, emptyText) {
      return (values || []).length
        ? values.map(value => `<code title="${escapeHtml(value)}">${escapeHtml(shortRef(value))}</code>`).join('<br>')
        : `<span class="ok">${escapeHtml(emptyText)}</span>`;
    }
    function shortRef(value) {
      const text = String(value);
      return text.length > 22 ? text.slice(0, 10) + '...' + text.slice(-7) : text;
    }
    function agentLabel(name) {
      return ({
        supervisor: '主管 Agent', market_agent: '市场调研 Agent', listing_agent: '商品文案 Agent',
        strategy_agent: '定价策略 Agent', analytics_agent: '销售分析 Agent',
        review_agent: '风险审核 Agent', browser_agent: '店铺执行 Agent'
      })[name] || name;
    }
    function statusLabel(status) {
      return ({created:'已创建', accepted:'已接收', running:'运行中', completed:'已完成', requires_revision:'需要修订', failed:'失败', rejected:'已拒绝', cancelled:'已取消'})[status] || status;
    }
    function statusClass(status) {
      return status === 'completed' ? 'ok' : (['failed', 'rejected', 'cancelled'].includes(status) ? 'bad' : 'warn');
    }
    function taskStatusLabel(status) {
      return ({created:'已创建', running:'运行中', waiting_for_input:'等待用户确认', awaiting_approval:'等待审批', waiting_for_approval:'等待审批', completed:'已完成', business_rejected:'业务条件未通过', technical_failed:'技术执行失败', needs_attention:'需要人工处理', failed:'失败'})[status] || status;
    }

    function priceDistribution(distribution) {
      const source = distribution || {};
      return {
        count: source.count,
        minimum: source.minimum,
        maximum: source.maximum,
        mean: source.mean,
        median: source.median,
        q1: source.q1,
        q3: source.q3
      };
    }

    function layerEvidence(layer, extra = {}) {
      if (!layer || !Object.keys(layer).length) {
        return '<span class="warn">该层证据尚未生成</span>';
      }
      return kv({
        sample_count: layer.sample_count,
        review_count: layer.review_count,
        price_distribution: priceDistribution(layer.price_distribution),
        ...extra,
        content_scope: '仅展示可审计聚合结果，不包含模型内部思维过程'
      });
    }

    function decisionAudit(decisions) {
      const audit = decisions.map(decision => ({
        sample_id: decision.sample_id,
        original_price: decision.original_price,
        normalized_price: decision.normalized_price,
        status: decision.status,
        excluded: decision.excluded,
        statistical_flags: decision.statistical_flags,
        reason_codes: decision.reason_codes,
        business_explanations: decision.business_explanations
      }));
      return `<pre>${escapeHtml(JSON.stringify(audit, null, 2))}</pre>`;
    }

    function layerAudit(decisions) {
      const grouped = decisions.reduce((summary, decision) => {
        const key = decision.assigned_layer || 'unknown';
        summary[key] = (summary[key] || 0) + 1;
        return summary;
      }, {});
      const notable = decisions
        .filter(decision => decision.assigned_layer !== 'core_comparable' || (decision.mismatch_reasons || []).length)
        .map(decision => ({
          sample_id: decision.sample_id,
          assigned_layer: decision.assigned_layer,
          adjacent_group: decision.adjacent_group,
          match_score: decision.match_score,
          mismatch_reasons: decision.mismatch_reasons
        }));
      return `${kv({layer_counts: grouped})}<pre>${escapeHtml(JSON.stringify(notable, null, 2))}</pre>`;
    }

    function item(title, html) {
      return `<div class="item"><h3>${escapeHtml(title)}</h3><p>${html || '-'}</p></div>`;
    }
    function pill(text) { return `<span class="pill">${escapeHtml(String(text))}</span>`; }
    function pills(values) { return values.map(pill).join(''); }
    function list(values) { return `<ul>${values.map(v => `<li>${escapeHtml(String(v))}</li>`).join('')}</ul>`; }
    function kv(obj) {
      return Object.entries(obj || {}).map(([k,v]) => `${escapeHtml(k)}: ${escapeHtml(JSON.stringify(v))}`).join('<br>');
    }
    function nodeList(nodes) {
      return Object.entries(nodes || {}).map(([k,v]) => `${escapeHtml(k)}: ${escapeHtml(v.status)}`).join('<br>');
    }
    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    async function loadLlmStatus() {
      const res = await fetch('/llm/status');
      const status = await res.json();
      const badge = document.getElementById('llmRuntime');
      badge.textContent = `LLM: ${status.provider}/${status.model}`;
      badge.className = 'pill ' + (status.ready ? 'ok' : 'warn');
      badge.title = status.issues.join(', ') || 'ready';
    }
    async function loadBrowserStatus() {
      const res = await fetch('/browser/status');
      const status = await res.json();
      const badge = document.getElementById('browserRuntime');
      badge.textContent = `Browser: ${status.backend}`;
      badge.className = 'pill ' + (status.ready ? 'ok' : 'warn');
      badge.title = status.issues.join(', ') || 'ready';
    }
    async function loadLinkedTask() {
      if (polling) return;
      polling = true;
      try {
        if (!pinnedTask) {
          if (!requestedTaskId) {
            const listResponse = await fetch('/tasks/checkpoints?limit=1');
            const tasks = await listResponse.json();
            watchedTaskId = tasks[0]?.task_id || '';
          }
        }
        if (watchedTaskId) {
          const response = await fetch('/tasks/' + encodeURIComponent(watchedTaskId));
          if (response.ok) {
            const state = await response.json();
            if (state.checkpoint_version !== lastRenderedVersion || state.run_id !== lastState?.run_id) {
              lastState = state;
              renderState(state);
            }
          }
        }
        await loadSellerCenter();
      } finally {
        polling = false;
      }
    }
    async function loadRunModelCalls(runId) {
      const target = document.getElementById('runModelCalls');
      if (!runId) { target.textContent = '-'; return; }
      try {
        const response = await fetch('/api/traces/' + encodeURIComponent(runId) + '/summary');
        if (!response.ok) throw new Error('trace unavailable');
        const summary = await response.json();
        if (lastState?.run_id === runId) target.textContent = summary.model_call_count ?? 0;
      } catch (_error) {
        if (lastState?.run_id === runId) target.textContent = '-';
      }
    }
    loadSellerCenter();
    loadLlmStatus();
    loadBrowserStatus();
    loadRelease();
    loadLinkedTask();
    setInterval(loadLinkedTask, 1500);
  </script>
</body>
</html>
"""
