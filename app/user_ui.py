from __future__ import annotations


USER_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EcomPilot 商品上新工作台</title>
  <style>
    :root {
      color-scheme: light;
      --ink:#172033;
      --muted:#667085;
      --line:#d7dde7;
      --soft:#f4f6f9;
      --panel:#ffffff;
      --accent:#087f73;
      --accent-soft:#eaf8f5;
      --blue:#2563eb;
      --blue-soft:#eff6ff;
      --warn:#9a5b13;
      --warn-soft:#fff7e8;
      --bad:#b42318;
      --bad-soft:#fff1f0;
      --ok:#067647;
    }
    * { box-sizing:border-box; }
    body {
      margin:0;
      min-width:320px;
      font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
      color:var(--ink);
      background:var(--soft);
      letter-spacing:0;
    }
    button,input,textarea { font:inherit; letter-spacing:0; }
    button,a { -webkit-tap-highlight-color:transparent; }
    header {
      min-height:64px;
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:20px;
      padding:0 24px;
      background:var(--panel);
      border-bottom:1px solid var(--line);
      position:sticky;
      top:0;
      z-index:5;
    }
    .brand { display:flex; align-items:baseline; gap:12px; min-width:0; }
    .brand strong { font-size:20px; white-space:nowrap; }
    .brand span { color:var(--muted); font-size:14px; white-space:nowrap; }
    nav { display:flex; align-items:center; gap:8px; }
    nav a {
      color:var(--muted);
      text-decoration:none;
      min-height:34px;
      display:inline-flex;
      align-items:center;
      padding:0 10px;
      border:1px solid transparent;
      border-radius:6px;
      font-size:13px;
    }
    nav a:hover { color:var(--ink); border-color:var(--line); }
    main {
      display:grid;
      grid-template-columns:minmax(320px,390px) minmax(0,1fr);
      max-width:1500px;
      margin:0 auto;
      min-height:calc(100vh - 65px);
      background:var(--panel);
      border-left:1px solid var(--line);
      border-right:1px solid var(--line);
    }
    .input-pane {
      border-right:1px solid var(--line);
      padding:22px;
      min-width:0;
      background:#fbfcfe;
    }
    .input-pane h1 { margin:0 0 6px; font-size:18px; }
    .input-pane > p { margin:0 0 20px; color:var(--muted); font-size:13px; }
    .form-grid { display:grid; grid-template-columns:1fr 1fr; gap:13px 12px; }
    .field { min-width:0; }
    .field.wide { grid-column:1/-1; }
    label { display:block; margin:0 0 6px; color:#475467; font-size:13px; }
    input,textarea {
      width:100%;
      border:1px solid #cbd3df;
      border-radius:6px;
      background:#fff;
      color:var(--ink);
      padding:9px 10px;
      outline:none;
    }
    input { height:40px; }
    textarea { min-height:82px; resize:vertical; line-height:1.5; }
    input:focus,textarea:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(8,127,115,.1); }
    .actions { display:flex; gap:9px; margin-top:18px; }
    button {
      min-height:40px;
      border:1px solid var(--line);
      border-radius:6px;
      padding:0 14px;
      color:var(--ink);
      background:#fff;
      cursor:pointer;
    }
    button.primary { flex:1; border-color:var(--accent); background:var(--accent); color:#fff; font-weight:600; }
    button.execute { border-color:var(--blue); background:var(--blue); color:#fff; font-weight:600; }
    button:disabled { cursor:wait; opacity:.58; }
    .service-status { display:grid; gap:8px; margin-top:20px; padding-top:17px; border-top:1px solid var(--line); }
    .service-row { display:flex; justify-content:space-between; gap:12px; font-size:12px; color:var(--muted); }
    .service-row strong { color:var(--ink); font-weight:500; }
    .result-pane { min-width:0; background:var(--panel); }
    .empty {
      min-height:calc(100vh - 65px);
      display:grid;
      place-items:center;
      padding:32px;
      color:var(--muted);
      text-align:center;
    }
    .empty strong { display:block; color:var(--ink); margin-bottom:6px; font-size:17px; }
    .busy-line { width:min(320px,70vw); height:3px; margin:16px auto 0; background:#dfe5ed; overflow:hidden; display:none; }
    .busy .busy-line { display:block; }
    .busy-line::after { content:""; display:block; width:42%; height:100%; background:var(--accent); animation:loading 1.2s infinite ease-in-out; }
    @keyframes loading { from { transform:translateX(-110%); } to { transform:translateX(340%); } }
    .result { display:none; }
    .result.visible { display:block; }
    .decision {
      padding:22px 26px;
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:18px;
      border-bottom:1px solid var(--line);
    }
    .decision.okay { background:var(--accent-soft); }
    .decision.blocked { background:var(--bad-soft); }
    .decision.synced { background:var(--blue-soft); }
    .decision h2 { margin:0 0 5px; font-size:20px; }
    .decision p { margin:0; color:#475467; font-size:14px; line-height:1.5; }
    .decision-actions { display:flex; gap:8px; flex:none; }
    .metrics { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); border-bottom:1px solid var(--line); }
    .metric { padding:15px 18px; border-right:1px solid var(--line); min-width:0; }
    .metric:last-child { border-right:0; }
    .metric small { display:block; color:var(--muted); margin-bottom:5px; }
    .metric strong { display:block; font-size:18px; overflow-wrap:anywhere; }
    .content { padding:22px 26px 34px; }
    .section-head { display:flex; justify-content:space-between; gap:12px; align-items:center; margin:0 0 12px; }
    .section-head h2 { margin:0; font-size:16px; }
    .section-head span { color:var(--muted); font-size:12px; }
    .section-block { margin-bottom:26px; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .output-card { border:1px solid var(--line); border-radius:8px; padding:15px; min-width:0; background:#fff; }
    .output-card.wide { grid-column:1/-1; }
    .output-card h3 { margin:0 0 9px; font-size:14px; }
    .output-card p { margin:0; color:#475467; line-height:1.65; font-size:13px; white-space:pre-wrap; overflow-wrap:anywhere; }
    .output-card ul { margin:0; padding-left:20px; color:#475467; }
    .output-card li { margin:5px 0; line-height:1.5; font-size:13px; }
    .tags { display:flex; flex-wrap:wrap; gap:7px; }
    .tag { border:1px solid var(--line); border-radius:999px; padding:3px 8px; color:#475467; font-size:12px; background:#fbfcfe; }
    .checks { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px 12px; }
    .check { color:#475467; font-size:13px; }
    .check.pass { color:var(--ok); }
    .suggestions { margin-top:12px; display:grid; gap:7px; }
    .suggestion { padding:9px 11px; border-left:3px solid var(--warn); background:var(--warn-soft); color:#69410e; font-size:13px; }
    .technical-failure { display:grid; gap:7px; color:var(--bad); }
    .technical-failure strong { font-size:14px; }
    .technical-failure p { color:#7a271a; }
    .evidence-link { color:var(--blue); text-decoration:none; font-size:13px; }
    .error-bar { display:none; margin-top:12px; padding:10px 12px; background:var(--bad-soft); color:var(--bad); border:1px solid #f5c7c2; border-radius:6px; font-size:13px; }
    @media(max-width:980px) {
      header { align-items:flex-start; padding:13px 16px; }
      .brand { flex-direction:column; gap:2px; }
      main { grid-template-columns:1fr; border:0; }
      .input-pane { border-right:0; border-bottom:1px solid var(--line); }
      .empty { min-height:360px; }
      .metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .metric { border-bottom:1px solid var(--line); }
      .grid { grid-template-columns:1fr; }
      .output-card.wide { grid-column:auto; }
    }
    @media(max-width:560px) {
      header { position:static; }
      nav a { padding:0 6px; }
      .form-grid { grid-template-columns:1fr; }
      .field.wide { grid-column:auto; }
      .decision { flex-direction:column; padding:18px; }
      .decision-actions { width:100%; }
      .decision-actions button { width:100%; }
      .content { padding:18px; }
      .checks { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="brand"><strong>EcomPilot</strong><span>商品上新工作台</span></div>
    <nav><a id="opsLink" href="/ops">运维后台</a><a id="traceLink" href="/traces">技术证据</a></nav>
  </header>
  <main>
    <aside class="input-pane">
      <h1>商品信息</h1>
      <p>生成文案、定价方案与执行前风险检查</p>
      <form id="productForm">
        <div class="form-grid">
          <div class="field"><label for="category">商品类别</label><input id="category" value="无线耳机" required /></div>
          <div class="field"><label for="audience">目标人群</label><input id="audience" value="大学生" required /></div>
          <div class="field wide"><label for="productFormFactor">已确认的产品形态</label><input id="productFormFactor" placeholder="例如：头戴式；不确定可留空" /></div>
          <div class="field"><label for="cost">单件成本（元）</label><input id="cost" type="number" min="0" step="0.01" value="95" required /></div>
          <div class="field"><label for="price">计划售价（元）</label><input id="price" type="number" min="0.01" step="0.01" value="199" required /></div>
          <div class="field"><label for="inventory">库存（件）</label><input id="inventory" type="number" min="0" step="1" value="800" required /></div>
          <div class="field"><label for="margin">最低毛利率（%）</label><input id="margin" type="number" min="0" max="99" step="0.1" value="25" required /></div>
          <div class="field wide"><label for="features">已确认的产品功能</label><textarea id="features">蓝牙5.3、游戏低延迟、长续航、快充、通话降噪</textarea></div>
          <div class="field wide"><label for="objective">运营目标</label><textarea id="objective">面向大学生完成首月冷启动，文案保持年轻、清晰、务实。</textarea></div>
        </div>
        <div class="actions">
          <button class="primary" id="generateButton" type="submit" disabled>生成上新方案</button>
          <button id="clearButton" type="button">清空结果</button>
        </div>
        <div class="error-bar" id="errorBar"></div>
      </form>
      <div class="service-status">
        <div class="service-row"><span>智能生成服务</span><strong id="llmStatus">检查中</strong></div>
        <div class="service-row"><span>店铺同步服务</span><strong id="browserStatus">检查中</strong></div>
      </div>
    </aside>
    <section class="result-pane">
      <div class="empty" id="emptyState">
        <div><strong id="emptyTitle">尚未生成方案</strong><span id="emptyText">商品方案将在这里显示</span><div class="busy-line"></div></div>
      </div>
      <div class="result" id="result">
        <div class="decision" id="decision">
          <div><h2 id="decisionTitle">-</h2><p id="decisionText">-</p></div>
          <div class="decision-actions">
            <button id="retryButton" type="button" style="display:none">重新生成方案</button>
            <button class="execute" id="executeButton" type="button">确认并同步到模拟店铺</button>
          </div>
        </div>
        <div class="metrics">
          <div class="metric"><small>售价</small><strong id="metricPrice">-</strong></div>
          <div class="metric"><small>优惠</small><strong id="metricCoupon">-</strong></div>
          <div class="metric"><small>预计到手价</small><strong id="metricNetPrice">-</strong></div>
          <div class="metric"><small>预计毛利率</small><strong id="metricMargin">-</strong></div>
          <div class="metric"><small>计划投入</small><strong id="metricUnits">-</strong></div>
        </div>
        <div class="content">
          <div class="section-block">
            <div class="section-head"><h2>商品页面方案</h2><span id="generationMode"></span></div>
            <div class="grid">
              <article class="output-card wide"><h3>商品标题</h3><p id="listingTitle">-</p></article>
              <article class="output-card"><h3>核心卖点</h3><div id="listingBullets"></div></article>
              <article class="output-card"><h3>搜索关键词</h3><div class="tags" id="listingKeywords"></div></article>
              <article class="output-card wide"><h3>表述与参数确认</h3><div id="complianceNotes"></div></article>
            </div>
          </div>
          <div class="section-block">
            <div class="section-head"><h2>定价与促销</h2></div>
            <div class="grid">
              <article class="output-card wide"><h3>首月方案</h3><p id="launchPlan">-</p></article>
              <article class="output-card"><h3>库存安排</h3><p id="inventoryPlan">-</p></article>
              <article class="output-card"><h3>市场参考</h3><p id="marketReference">-</p></article>
              <article class="output-card wide"><h3>本次决策参考</h3><div id="strategyEvidence">-</div></article>
            </div>
          </div>
          <div class="section-block">
            <div class="section-head"><h2>风险与修改建议</h2></div>
            <div class="grid">
              <article class="output-card wide"><div id="riskContent"></div><div class="suggestions" id="suggestions"></div></article>
            </div>
          </div>
          <div class="section-block" id="syncSection">
            <div class="section-head"><h2>店铺同步结果</h2><a class="evidence-link" id="resultTraceLink" href="/traces">查看技术执行证据</a></div>
            <div class="grid"><article class="output-card wide"><div id="syncContent"></div></article></div>
          </div>
        </div>
      </div>
    </section>
  </main>
  <script>
    let currentState = null;
    let realLlmReady = false;
    let realBrowserReady = false;
    const violationLabels = {
      margin_below_minimum: '预计毛利率低于你的最低要求',
      inventory_shortage: '计划销售数量超过可用库存',
      absolute_marketing_term: '商品标题包含不适合使用的绝对化宣传词',
      discount_unit_mismatch: '优惠金额被错误描述成了百分比折扣'
    };
    const agentLabels = {
      market_agent: '市场调研',
      listing_agent: '商品文案生成',
      strategy_agent: '定价与促销策略',
      review_agent: '风险审核',
      browser_agent: '店铺同步'
    };

    const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const money = value => value == null ? '-' : `${Number(value).toFixed(Number(value) % 1 ? 2 : 0)} 元`;
    const percent = value => value == null ? '-' : `${(Number(value) * 100).toFixed(2)}%`;
    const listHtml = values => values?.length ? `<ul>${values.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : '<p>暂无</p>';
    const tagsHtml = values => values?.length ? values.map(item => `<span class="tag">${esc(item)}</span>`).join('') : '<span class="tag">暂无</span>';

    document.getElementById('productForm').addEventListener('submit', event => {
      event.preventDefault();
      generatePlan();
    });
    document.getElementById('executeButton').addEventListener('click', approveAndSync);
    document.getElementById('retryButton').addEventListener('click', generatePlan);
    document.getElementById('clearButton').addEventListener('click', clearResult);

    function buildGoal() {
      const category = document.getElementById('category').value.trim();
      const audience = document.getElementById('audience').value.trim();
      const productFormFactor = document.getElementById('productFormFactor').value.trim();
      const cost = document.getElementById('cost').value;
      const price = document.getElementById('price').value;
      const inventory = document.getElementById('inventory').value;
      const margin = document.getElementById('margin').value;
      const features = document.getElementById('features').value.trim();
      const objective = document.getElementById('objective').value.trim();
      return `我要上架一款成本 ${cost} 元的${category}，目标售价 ${price} 元，主要面向${audience}，库存 ${inventory} 件，毛利率不能低于 ${margin}%。已确认的产品功能：${features || '暂无补充'}。已确认的产品形态：${productFormFactor || '未确认'}。运营目标：${objective || '生成安全可执行的上新方案'}。`;
    }

    async function generatePlan() {
      if (!realLlmReady) {
        showError('DeepSeek 真实模型服务未连接，用户方案不会使用规则结果代替。请从同一终端重新启动联动服务。');
        return;
      }
      if (!realBrowserReady) {
        showError('Playwright 店铺同步服务未连接。请先恢复联动服务，再生成新的用户方案。');
        return;
      }
      setBusy(true, '正在生成商品方案');
      try {
        const response = await fetch('/user/tasks/run', {
          method:'POST',
          headers:{'content-type':'application/json'},
          body:JSON.stringify({
            goal:buildGoal(),
            approval:{approved:false,approver:'user-workspace',reason:'review before sync'}
          })
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(readError(payload, '方案生成失败'));
        currentState = payload;
        renderState(payload);
      } catch (error) {
        showError(error.message || String(error));
      } finally {
        setBusy(false);
      }
    }

    async function approveAndSync() {
      if (!currentState || currentState.status !== 'waiting_for_approval') return;
      if (!realBrowserReady) {
        showError('Playwright 店铺同步服务未连接，本次方案不会使用 Mock Browser 执行。');
        return;
      }
      setBusy(true, '正在同步并核对店铺页面');
      try {
        const response = await fetch(`/user/tasks/${encodeURIComponent(currentState.task_id)}/resume`, {
          method:'POST',
          headers:{'content-type':'application/json'},
          body:JSON.stringify({
            approval:{approved:true,approver:'user-workspace',reason:'user confirmed generated plan'},
            expected_checkpoint_version:currentState.checkpoint_version,
            requested_by:'user-workspace'
          })
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(readError(payload, '店铺同步失败'));
        currentState = payload;
        renderState(payload);
      } catch (error) {
        showError(error.message || String(error));
      } finally {
        setBusy(false);
      }
    }

    function renderState(state) {
      const market = state.agent_outputs?.market_agent || {};
      const listing = state.agent_outputs?.listing_agent || {};
      const strategy = state.agent_outputs?.strategy_agent || {};
      const review = state.agent_outputs?.review_agent || {};
      const browser = state.agent_outputs?.browser_agent || {};
      const margin = strategy.margin || {};
      const inventory = strategy.inventory_check || {};
      const verification = browser.verification || {};
      const revisionLoop = state.workflow_loops?.compliance_repair || state.workflow_loops?.listing_review || null;
      const semanticCorrectionCount = (listing.semantic_corrections || []).length
        + (strategy.semantic_corrections || []).length;
      const listingExecuted = Object.keys(listing).length > 0;
      const strategyExecuted = Object.keys(strategy).length > 0;
      const reviewExecuted = Object.keys(review).length > 0;
      const waiting = state.status === 'waiting_for_approval';
      const completed = state.status === 'completed';
      const outcome = state.presentation?.outcome || state.outcome;
      const businessRejected = outcome === 'business_rejected'
        || (state.status === 'failed' && reviewExecuted && review.approved_for_execution === false);
      const technicalFailure = outcome === 'technical_failed'
        || (state.status === 'failed' && !businessRejected);
      const blocked = businessRejected || technicalFailure;
      const failure = state.status === 'failed' ? taskFailureInfo(state, businessRejected) : null;

      document.getElementById('emptyState').style.display = 'none';
      document.getElementById('result').classList.add('visible');
      const taskQuery = `?task_id=${encodeURIComponent(state.task_id)}`;
      const runQuery = `?run_id=${encodeURIComponent(state.run_id)}`;
      document.getElementById('opsLink').href = `/ops${taskQuery}&pin=1`;
      document.getElementById('traceLink').href = `/traces${runQuery}`;
      document.getElementById('resultTraceLink').href = `/traces${runQuery}`;
      const decision = document.getElementById('decision');
      decision.className = `decision ${completed ? 'synced' : blocked ? 'blocked' : 'okay'}`;
      document.getElementById('decisionTitle').textContent = completed
        ? '已同步并完成核对'
        : technicalFailure
          ? '系统执行遇到技术问题'
          : businessRejected
            ? '当前方案不满足执行条件'
            : '方案可执行，等待你的确认';
      document.getElementById('decisionText').textContent = completed
        ? '商品信息和优惠方案已写入模拟店铺，页面回读结果一致。'
        : technicalFailure
          ? `${failure.agentLabel}未能完成：${failure.message} 店铺内容没有被修改。`
          : businessRejected
            ? `${failure.agentLabel}未通过：${failure.message} 店铺内容没有被修改。`
          : revisionLoop?.phase === 'completed'
            ? `方案曾被审核拦截，系统已自动修订并复核通过 ${revisionLoop.iteration} 次；确认后才会同步到模拟店铺。`
            : semanticCorrectionCount
              ? `文案、定价和风险检查已完成；系统已自动校正 ${semanticCorrectionCount} 处不一致表述，确认后才会同步到模拟店铺。`
              : '文案、定价和风险检查已完成；确认后才会同步到模拟店铺。';
      document.getElementById('executeButton').style.display = waiting && review.approved_for_execution ? 'inline-block' : 'none';
      document.getElementById('executeButton').disabled = !(waiting && review.approved_for_execution && realBrowserReady);
      document.getElementById('retryButton').style.display = state.status === 'failed' ? 'inline-block' : 'none';

      document.getElementById('metricPrice').textContent = money(strategy.price);
      document.getElementById('metricCoupon').textContent = money(strategy.coupon);
      document.getElementById('metricNetPrice').textContent = money(margin.net_price);
      document.getElementById('metricMargin').textContent = percent(margin.margin_rate);
      document.getElementById('metricUnits').textContent = strategy.planned_units == null ? '-' : `${strategy.planned_units} 件`;
      document.getElementById('generationMode').textContent = !listingExecuted
        ? '尚未执行'
        : strategy.strategy_render_version
          ? `智能生成 · 关键数字已校验${semanticCorrectionCount ? ` · 已安全校正 ${semanticCorrectionCount} 处` : ''}`
        : ['llm_revision', 'safe_revision'].includes(listing.generation_mode)
          ? `智能生成 · 已自动修订 ${listing.revision_iteration || 1} 次`
          : semanticCorrectionCount
            ? `智能生成 · 已安全校正 ${semanticCorrectionCount} 处`
          : listing.generation_mode === 'llm'
          ? '智能生成'
          : '规则生成';

      document.getElementById('listingTitle').textContent = listingExecuted ? listing.title || '-' : '尚未执行';
      document.getElementById('listingBullets').innerHTML = listingExecuted ? listHtml(listing.bullets) : '<p>尚未执行</p>';
      document.getElementById('listingKeywords').innerHTML = listingExecuted ? tagsHtml(listing.keywords) : '<span class="tag">尚未执行</span>';
      document.getElementById('complianceNotes').innerHTML = listingExecuted ? listHtml(listing.compliance_notes) : '<p>尚未执行</p>';
      document.getElementById('launchPlan').textContent = strategyExecuted ? strategy.launch_plan || '-' : '尚未执行';
      document.getElementById('inventoryPlan').textContent = !strategyExecuted
        ? '尚未执行'
        : inventory.valid === false
        ? `库存不足：现有 ${inventory.inventory ?? 0} 件，计划使用 ${inventory.planned_units ?? 0} 件。`
        : `现有库存 ${inventory.inventory ?? '-'} 件，首批计划 ${inventory.planned_units ?? '-'} 件，预计剩余 ${inventory.remaining ?? '-'} 件。`;
      document.getElementById('marketReference').textContent = market.price_band
        ? `参考价格区间 ${market.price_band[0]} 至 ${market.price_band[1]} 元，中位价格 ${market.median_price} 元；已参考 ${market.sample_size?.competitors ?? 0} 个商品样本。${market.evidence_status === 'degraded' ? ' 本次智能市场查询未完成，已使用基础市场样本继续生成。' : ''}`
        : '市场调研尚未完成。';
      document.getElementById('strategyEvidence').innerHTML = strategyExecuted
        ? renderStrategyEvidence(
            strategy.selected_evidence_tools || [],
            strategy.decision_evidence || {},
            strategy
          )
        : '<p>策略尚未执行</p>';

      const violations = review.violations || [];
      const notes = review.review_notes || [];
      const findings = review.review_findings || [];
      const findingMessages = Object.fromEntries(
        findings.map(item => [`llm_review:${item.code}`, item.message])
      );
      document.getElementById('riskContent').innerHTML = state.status === 'failed'
        ? renderTaskFailure(failure)
        : !reviewExecuted
          ? '<p>审核尚未执行</p>'
          : violations.length
            ? listHtml(violations.map(item => violationLabels[item] || findingMessages[item] || item))
            : notes.length
              ? listHtml(notes)
              : '<p style="color:var(--ok)">未发现阻止执行的价格、库存或标题风险。</p>';
      document.getElementById('suggestions').innerHTML = businessRejected ? buildSuggestions(state.constraints || {}, margin) : '';

      document.getElementById('syncContent').innerHTML = completed && verification.verified
        ? renderVerification(verification.checks || {}, browser.browser_result || {})
        : waiting
          ? '<p>尚未同步。确认方案后，系统才会操作模拟店铺。</p>'
          : '<p>本次方案未执行，店铺内容没有被修改。</p>';
      hideError();
    }

    function taskFailureInfo(state, businessRejected = false) {
      const contracted = state.presentation?.failure || state.failure;
      if (contracted) {
        return {
          agentName: contracted.agent_name || 'unknown',
          agentLabel: agentLabels[contracted.agent_name] || '任务处理环节',
          code: contracted.code,
          message: contracted.user_message,
          details: []
        };
      }
      const failedNode = Object.values(state.nodes || {}).find(node => node.status === 'failed') || {};
      const failedHandoff = [...(state.handoffs || [])].reverse().find(item =>
        item.status === 'failed' || item.error
      );
      const failedDelegation = Object.values(state.a2a_delegations || {}).reverse().find(item =>
        ['failed', 'rejected', 'cancelled'].includes(item.status) || item.error
      );
      const review = state.agent_outputs?.review_agent || {};
      const blockingFinding = (review.review_findings || []).find(item => item.blocking);
      const firstViolation = (review.violations || [])[0];
      const agentName = businessRejected
        ? 'review_agent'
        : failedNode.agent_name
          || failedHandoff?.source_agent
          || failedDelegation?.request?.receiver_agent
          || 'unknown';
      const modelRecord = [...(state.model_records || [])].reverse().find(record =>
        (record.agent_name === agentName || agentName === 'unknown')
        && (record.status === 'failed' || record.structured_validation === 'failed' || record.error)
      );
      const toolRecord = [...(state.tool_records || [])].reverse().find(record =>
        (record.agent_name === agentName || agentName === 'unknown')
        && (record.status === 'failed' || record.error)
      );
      const stoppedLoop = Object.values(state.workflow_loops || {}).find(loop =>
        loop.phase === 'exhausted' || loop.stop_reason
      );
      const reviewReason = blockingFinding?.message
        || violationLabels[firstViolation]
        || firstViolation;
      const rawError = errorText(
        (businessRejected ? reviewReason : null)
        || failedHandoff?.error
        || failedDelegation?.error
        || modelRecord?.validation_error
        || modelRecord?.error
        || toolRecord?.error
        || stoppedLoop?.stop_reason
        || '该环节未能生成符合系统要求的结果。'
      );
      const detailCandidates = [
        reviewReason,
        ...(review.violations || []).map(item => violationLabels[item] || item),
        failedHandoff?.error,
        failedDelegation?.error,
        modelRecord?.validation_error,
        modelRecord?.error,
        toolRecord?.error,
        stoppedLoop?.stop_reason
      ]
        .filter(Boolean)
        .map(item => friendlyFailureError(errorText(item)));
      return {
        agentName,
        agentLabel: agentLabels[agentName] || '任务处理环节',
        code: blockingFinding?.code
          || firstViolation
          || modelRecord?.error_type
          || toolRecord?.error_type
          || stoppedLoop?.stop_reason
          || 'workflow_failed',
        message: friendlyFailureError(rawError),
        details: [...new Set(detailCandidates)]
      };
    }

    function errorText(value) {
      if (typeof value === 'string') return value;
      if (value && typeof value === 'object') return value.message || JSON.stringify(value);
      return String(value || '未知技术错误');
    }

    function friendlyFailureError(error) {
      if (violationLabels[error]) return violationLabels[error];
      const normalized = error.toLowerCase();
      if (normalized.includes('not valid json') || normalized.includes('structured')) {
        return '模型返回的结果格式不符合结构化要求，系统未能获得通过校验的结构化结果。';
      }
      if (normalized.includes('discount_unit_mismatch') || normalized.includes('discount_representation_mismatch')) {
        return '促销文案把优惠金额写成了百分比，自动修订后仍未通过审核。';
      }
      if (normalized.includes('margin_below_minimum')) return violationLabels.margin_below_minimum;
      if (normalized.includes('inventory_shortage')) return violationLabels.inventory_shortage;
      if (normalized.includes('revision_budget_exhausted')) return '自动修改已达到次数上限，但复核仍未通过。';
      if (normalized.includes('missing_revision_target')) return '审核要求修改方案，但没有找到负责返工的处理环节。';
      if (normalized.includes('incomplete')) return '模型在限定输出长度内没有返回完整结果。';
      if (normalized.includes('timeout')) return '外部服务响应超时，请稍后重新生成。';
      if (normalized.includes('budget')) return '本次模型或工具调用次数已达到安全上限。';
      if (normalized.includes('react step limit')) return '智能体已达到探索上限，但没有按要求提交最终结果。';
      if (normalized.includes('sql policy') || normalized.includes('data access')) return '市场查询没有通过只读数据访问检查。';
      if (normalized.includes('401') || normalized.includes('403')) return '模型服务身份验证失败，请检查 API Key。';
      if (normalized.includes('429')) return '模型服务当前请求过多，请稍后重试。';
      return error;
    }

    function renderTaskFailure(failure) {
      const otherDetails = (failure.details || []).filter(item => item !== failure.message);
      return `<div class="technical-failure"><strong>失败环节：${esc(failure.agentLabel)}</strong><p>具体原因：${esc(failure.message)}</p><p>错误标识：${esc(failure.code)}</p>${otherDetails.length ? listHtml(otherDetails) : ''}</div>`;
    }

    function buildSuggestions(constraints, margin) {
      const cost = Number(constraints.cost);
      const price = Number(constraints.target_price);
      const minimum = Number(constraints.min_margin_rate);
      const suggestions = [];
      if (margin.margin_rate != null && margin.margin_rate < minimum && minimum < 1) {
        suggestions.push(`将售价提高到至少 ${(cost / (1 - minimum)).toFixed(2)} 元`);
        suggestions.push(`或将单件成本控制在 ${(price * (1 - minimum)).toFixed(2)} 元以内`);
      }
      return suggestions.map(item => `<div class="suggestion">${esc(item)}</div>`).join('');
    }

    function renderStrategyEvidence(selected, evidence, strategy={}) {
      const labels = {
        forecast_demand: '需求预测',
        query_campaign_history: '历史活动',
        analyze_competitor_price_trends: '竞品价格变化'
      };
      if (!selected.length) {
        return listHtml(['本次未额外调用可选策略证据。']);
      }
      const summaries = selected.map(name => {
        const value = evidence[name] || {};
        if (name === 'forecast_demand') return `${labels[name]}：未来 ${value.horizon_days ?? '-'} 天基准需求约 ${value.forecast_units ?? '-'} 件`;
        if (name === 'query_campaign_history') return `${labels[name]}：参考 ${value.summary?.sample_size ?? 0} 次相似活动`;
        if (name === 'analyze_competitor_price_trends') return `${labels[name]}：参考 ${value.summary?.sample_size ?? 0} 个竞品，${value.summary?.price_cuts ?? 0} 个近期降价`;
        return labels[name] || name;
      });
      return listHtml(summaries);
    }

    function renderVerification(checks, execution) {
      const labels = {
        product_exists:'商品已创建',title_match:'标题一致',price_match:'售价一致',stock_match:'库存一致',
        bullets_match:'卖点一致',coupon_match:'优惠一致',promotion_exists:'促销已创建',promotion_coupon_match:'促销金额一致'
      };
      const rows = Object.entries(checks).map(([key,value]) => `<div class="check ${value ? 'pass' : ''}">${value ? '通过' : '未通过'} · ${esc(labels[key] || key)}</div>`).join('');
      return `<div class="checks">${rows}</div><p style="margin-top:12px">本次共完成 ${esc(execution.actions?.length ?? 0)} 个浏览器动作。</p>`;
    }

    function setBusy(busy, title='') {
      document.querySelectorAll('button').forEach(button => button.disabled = busy);
      if (busy) {
        document.getElementById('result').classList.remove('visible');
        document.getElementById('emptyState').style.display = 'grid';
        document.getElementById('emptyState').classList.add('busy');
        document.getElementById('emptyTitle').textContent = title;
        document.getElementById('emptyText').textContent = '请稍候';
      } else {
        document.getElementById('emptyState').classList.remove('busy');
        if (currentState) {
          document.getElementById('emptyState').style.display = 'none';
          document.getElementById('result').classList.add('visible');
        }
        document.getElementById('generateButton').disabled = !(realLlmReady && realBrowserReady);
        document.getElementById('clearButton').disabled = false;
        const review = currentState?.agent_outputs?.review_agent || {};
        document.getElementById('executeButton').disabled = !(
          currentState?.status === 'waiting_for_approval'
          && review.approved_for_execution
          && realBrowserReady
        );
        document.getElementById('retryButton').disabled = !(
          currentState?.status === 'failed'
          && realLlmReady
          && realBrowserReady
        );
      }
    }

    function clearResult() {
      currentState = null;
      document.getElementById('result').classList.remove('visible');
      document.getElementById('emptyState').style.display = 'grid';
      document.getElementById('emptyTitle').textContent = '尚未生成方案';
      document.getElementById('emptyText').textContent = '商品方案将在这里显示';
      hideError();
    }
    function showError(message) {
      const bar = document.getElementById('errorBar');
      bar.textContent = message;
      bar.style.display = 'block';
      document.getElementById('emptyTitle').textContent = '请求未完成';
      document.getElementById('emptyText').textContent = message;
    }
    function hideError() { document.getElementById('errorBar').style.display = 'none'; }
    function readError(payload, fallback) {
      const detail = payload?.detail;
      if (typeof detail === 'string') return detail;
      if (detail?.issues?.length) return `联动服务未就绪：${detail.issues.join('、')}`;
      if (detail?.message || detail?.error || detail?.reason) return detail.message || detail.error || detail.reason;
      if (payload?.message || payload?.error || payload?.reason) return payload.message || payload.error || payload.reason;
      return fallback;
    }

    async function loadServiceStatus() {
      try {
        const response = await fetch('/linked/status');
        const linked = await response.json();
        const llm = linked.llm || {};
        const browser = linked.browser || {};
        realLlmReady = Boolean(llm.ready && llm.real_llm_enabled && llm.provider === 'deepseek');
        realBrowserReady = Boolean(browser.ready && browser.real_browser_enabled && browser.backend === 'playwright');
        document.getElementById('llmStatus').textContent = realLlmReady ? `已连接 · ${llm.model}` : '未连接真实模型';
        document.getElementById('browserStatus').textContent = realBrowserReady ? '已连接 · Playwright' : '未连接真实浏览器';
        document.getElementById('generateButton').disabled = !(realLlmReady && realBrowserReady && linked.ready);
      } catch (_error) {
        realLlmReady = false;
        realBrowserReady = false;
        document.getElementById('llmStatus').textContent = '不可用';
        document.getElementById('browserStatus').textContent = '不可用';
        document.getElementById('generateButton').disabled = true;
      }
    }
    loadServiceStatus();
  </script>
</body>
</html>
"""

# V28 keeps this compatibility import while the persistent conversation UI lives separately.
from app.copilot_ui import COPILOT_HTML

USER_HTML = COPILOT_HTML
