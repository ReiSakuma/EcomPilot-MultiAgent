from __future__ import annotations


COPILOT_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EcomPilot 对话式运营工作台</title>
  <style>
    :root {
      color-scheme:light; --ink:#172033; --muted:#667085; --line:#d8dee8;
      --soft:#f4f6f9; --panel:#fff; --accent:#087f73; --accent-soft:#eaf8f5;
      --blue:#2563eb; --blue-soft:#eff6ff; --bad:#b42318; --bad-soft:#fff1f0;
      --warn:#9a5b13; --warn-soft:#fff7e8; --ok:#067647;
    }
    * { box-sizing:border-box; }
    body { margin:0; min-width:320px; color:var(--ink); background:var(--soft); font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; letter-spacing:0; }
    button,input,select,textarea { font:inherit; letter-spacing:0; }
    button { min-height:38px; border:1px solid var(--line); border-radius:6px; padding:0 13px; color:var(--ink); background:#fff; cursor:pointer; }
    button:hover { border-color:#9aa6b6; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    button.primary { border-color:var(--accent); background:var(--accent); color:#fff; font-weight:600; }
    button.execute { border-color:var(--blue); background:var(--blue); color:#fff; font-weight:600; }
    a { color:var(--blue); text-decoration:none; }
    header { height:62px; display:flex; align-items:center; justify-content:space-between; gap:18px; padding:0 22px; background:#fff; border-bottom:1px solid var(--line); }
    .brand { display:flex; align-items:baseline; gap:10px; min-width:0; }
    .brand strong { font-size:20px; }
    .brand span { color:var(--muted); font-size:13px; }
    nav { display:flex; gap:8px; }
    nav a { padding:8px 9px; color:#475467; font-size:13px; }
    .history-toggle { display:none; width:34px; min-height:34px; padding:0; font-size:19px; }
    .layout { max-width:1680px; height:calc(100vh - 62px); min-height:560px; margin:0 auto; display:grid; grid-template-columns:238px minmax(360px,460px) minmax(540px,1fr); overflow:hidden; background:#fff; border-left:1px solid var(--line); border-right:1px solid var(--line); }
    .history { min-width:0; min-height:0; padding:18px 14px; overflow-y:auto; overflow-x:hidden; border-right:1px solid var(--line); background:#f8fafc; }
    .pane-title { display:flex; align-items:center; justify-content:space-between; margin-bottom:13px; }
    .pane-title h2 { margin:0; font-size:15px; }
    .pane-actions { display:flex; align-items:center; gap:6px; }
    .new-button { width:34px; min-height:34px; padding:0; font-size:20px; line-height:1; }
    .history-group { margin:18px 4px 8px; color:var(--muted); font-size:11px; text-transform:uppercase; }
    .history-item { display:block; width:100%; min-width:0; max-width:100%; min-height:64px; padding:10px; overflow:hidden; text-align:left; border-color:var(--line); background:#fff; }
    .history-item.active { border-color:#9fd4cd; background:var(--accent-soft); }
    .history-item strong,.history-item span { display:block; width:100%; min-width:0; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .history-item strong { margin-bottom:5px; font-size:13px; }
    .history-item span { color:var(--muted); font-size:11px; }
    .history-empty { padding:12px 9px; color:var(--muted); font-size:12px; line-height:1.6; }
    .history-list { display:grid; width:100%; min-width:0; gap:7px; overflow:hidden; }
    .history-time { margin-top:3px; color:#98a2b3 !important; font-size:10px !important; }
    .history-tools { display:grid; gap:7px; margin-bottom:12px; }
    .history-tools input,.history-tools select { width:100%; height:34px; border:1px solid var(--line); border-radius:5px; padding:0 9px; color:var(--ink); background:#fff; font-size:12px; }
    .history-filter-row { display:grid; grid-template-columns:1fr 1fr; gap:7px; }
    .conversation { display:grid; grid-template-rows:auto minmax(0,1fr) auto; min-width:0; min-height:0; border-right:1px solid var(--line); }
    .conversation-head { padding:16px 18px; border-bottom:1px solid var(--line); }
    .conversation-head > div { min-width:0; }
    .conversation-head h1 { margin:0 0 4px; font-size:17px; }
    .conversation-head p { margin:0; color:var(--muted); font-size:12px; }
    .sidebar-toggle { display:inline-grid; flex:0 0 34px; width:34px; min-height:34px; padding:0; place-items:center; font-size:22px; line-height:1; }
    .messages { min-height:0; overflow:auto; padding:18px; display:flex; flex-direction:column; gap:14px; background:#fbfcfe; }
    .message { max-width:92%; }
    .message.user { align-self:flex-end; }
    .message.assistant { align-self:flex-start; }
    .bubble { border:1px solid var(--line); border-radius:8px; padding:12px 14px; line-height:1.65; font-size:13px; white-space:pre-wrap; overflow-wrap:anywhere; background:#fff; }
    .user .bubble { border-color:#b5d9d4; background:var(--accent-soft); }
    .message-meta { margin:5px 3px 0; color:var(--muted); font-size:11px; }
    .message-approval { width:100%; margin-top:10px; border-color:var(--blue); color:var(--blue); font-weight:600; }
    .live-status { display:grid; gap:7px; min-width:260px; }
    .live-row { display:grid; grid-template-columns:10px 1fr; gap:8px; color:#475467; font-size:12px; }
    .live-dot { width:8px; height:8px; margin-top:6px; border-radius:50%; background:#98a2b3; }
    .live-row.running .live-dot { background:var(--accent); box-shadow:0 0 0 4px var(--accent-soft); }
    .live-row.failed .live-dot { background:var(--bad); }
    .mobile-modes { display:none; }
    .example { margin-top:9px; width:100%; height:auto; min-height:36px; padding:8px 10px; text-align:left; color:#475467; font-size:12px; line-height:1.5; }
    .quick-actions { display:grid; gap:6px; margin-top:10px; }
    .quick-actions .example { margin:0; }
    .action-summary { margin-top:9px; border-top:1px solid #e7ebf0; padding-top:9px; display:grid; gap:6px; }
    .action-row { display:grid; grid-template-columns:16px 1fr; gap:7px; color:#475467; font-size:12px; }
    .action-mark { color:var(--muted); }
    .action-row.completed .action-mark { color:var(--ok); }
    .action-row.failed .action-mark { color:var(--bad); }
    .composer { border-top:1px solid var(--line); padding:13px 16px 15px; background:#fff; }
    .composer textarea { width:100%; min-height:84px; max-height:190px; resize:vertical; padding:10px 11px; border:1px solid #cbd3df; border-radius:6px; color:var(--ink); outline:none; line-height:1.5; }
    .composer textarea:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(8,127,115,.1); }
    .composer-actions { margin-top:9px; display:flex; align-items:flex-end; justify-content:space-between; gap:10px; }
    .composer-actions .primary { flex:0 0 72px; white-space:nowrap; }
    .service { min-width:0; display:flex; flex-wrap:wrap; gap:6px; color:var(--muted); font-size:11px; }
    .service span { border:1px solid var(--line); border-radius:999px; padding:3px 7px; }
    .service span.ready { border-color:#a6d7c8; color:var(--ok); background:#f0fbf7; }
    .service span.degraded { border-color:#efd6a7; color:var(--warn); background:var(--warn-soft); }
    .service span.failed { border-color:#f3c6c2; color:var(--bad); background:var(--bad-soft); }
    .workspace { min-width:0; min-height:0; background:#fff; overflow:auto; }
    .workspace-empty { min-height:calc(100vh - 62px); display:grid; place-items:center; padding:32px; text-align:center; color:var(--muted); }
    .workspace-empty strong { display:block; margin-bottom:5px; color:var(--ink); font-size:17px; }
    .workspace-content { display:none; }
    .workspace-content.visible { display:block; }
    .decision { padding:18px 22px; display:flex; align-items:flex-start; justify-content:space-between; gap:16px; border-bottom:1px solid var(--line); background:var(--accent-soft); }
    .decision.completed { background:var(--blue-soft); }
    .decision.failed,.decision.technical_failed { background:var(--bad-soft); }
    .decision.business_rejected,.decision.waiting_for_input { background:var(--warn-soft); }
    .decision h2 { margin:0 0 4px; font-size:18px; }
    .decision p { margin:0; color:#475467; font-size:12px; line-height:1.5; }
    .decision-meta { margin-top:7px; display:flex; flex-wrap:wrap; gap:6px; }
    .decision-meta span { border:1px solid rgba(71,84,103,.22); border-radius:999px; padding:3px 7px; color:#475467; background:rgba(255,255,255,.62); font-size:11px; }
    .price-confirmation { display:none; padding:18px 22px; border-bottom:1px solid var(--line); background:#fffdf8; }
    .price-confirmation.visible { display:block; }
    .price-confirmation h2 { margin:0 0 5px; font-size:16px; }
    .price-confirmation > p { margin:0 0 13px; color:#475467; font-size:12px; line-height:1.6; }
    .price-facts { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid #ead8b7; border-radius:7px; overflow:hidden; background:#fff; }
    .price-fact { min-width:0; padding:11px 12px; border-right:1px solid #ead8b7; }
    .price-fact:last-child { border-right:0; }
    .price-fact small { display:block; margin-bottom:4px; color:var(--muted); font-size:10px; }
    .price-fact strong { display:block; font-size:14px; overflow-wrap:anywhere; }
    .price-options { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; margin-top:12px; }
    .price-option { min-width:0; padding:12px; border:1px solid var(--line); border-radius:7px; background:#fff; }
    .price-option h3 { margin:0 0 5px; font-size:13px; }
    .price-option p { min-height:38px; margin:0 0 9px; color:var(--muted); font-size:11px; line-height:1.55; }
    .price-option input { width:100%; height:34px; margin-bottom:8px; border:1px solid var(--line); border-radius:5px; padding:0 8px; font-size:12px; }
    .price-option button { width:100%; min-height:35px; }
    .price-option .recommended { border-color:var(--accent); background:var(--accent); color:#fff; font-weight:600; }
    .price-option-error { display:none; margin-top:9px; color:var(--bad); font-size:12px; }
    .batch-selection { display:none; margin-top:12px; padding-top:10px; border-top:1px solid rgba(71,84,103,.18); }
    .batch-selection.visible { display:grid; gap:7px; }
    .batch-selection strong { font-size:12px; }
    .batch-option { display:flex; align-items:center; gap:8px; color:#344054; font-size:12px; }
    .batch-option input { width:16px; height:16px; margin:0; }
    .batch-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .execution-receipt { display:none; align-items:center; gap:9px; padding:10px 22px; border-bottom:1px solid var(--line); color:#475467; background:#f8fafc; font-size:12px; }
    .execution-receipt.visible { display:flex; }
    .execution-receipt::before { content:""; flex:0 0 8px; width:8px; height:8px; border-radius:50%; background:#98a2b3; }
    .execution-receipt.running::before { background:var(--accent); box-shadow:0 0 0 4px var(--accent-soft); }
    .execution-receipt.completed::before { background:var(--ok); }
    .execution-receipt.failed::before { background:var(--bad); }
    .retry-batch { border:1px solid #d0d5dd; background:#fff; color:#344054; padding:10px 14px; font-weight:700; cursor:pointer; }
    .metrics { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); border-bottom:1px solid var(--line); }
    .metric { min-width:0; padding:13px 15px; border-right:1px solid var(--line); }
    .metric:last-child { border-right:0; }
    .metric small { display:block; margin-bottom:4px; color:var(--muted); font-size:11px; }
    .metric strong { font-size:17px; overflow-wrap:anywhere; }
    .tabs { display:flex; border-bottom:1px solid var(--line); padding:0 18px; overflow:auto; background:#fff; }
    .tab { min-height:56px; border:0; border-bottom:2px solid transparent; border-radius:0; padding:3px 14px 0; color:var(--muted); white-space:nowrap; }
    .tab.active { border-bottom-color:var(--accent); color:var(--accent); }
    .work-body { padding:18px 22px 32px; }
    .tab-panel { display:none; }
    .tab-panel.active { display:block; }
    .section-title { margin:0 0 11px; font-size:15px; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:11px; }
    .card { min-width:0; border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }
    .card.wide { grid-column:1/-1; }
    .card h3 { margin:0 0 8px; font-size:13px; }
    .card p { margin:0; color:#475467; line-height:1.65; font-size:12px; white-space:pre-wrap; overflow-wrap:anywhere; }
    .card ul { margin:0; padding-left:19px; color:#475467; }
    .card li { margin:4px 0; line-height:1.5; font-size:12px; }
    .tags { display:flex; flex-wrap:wrap; gap:6px; }
    .tag { border:1px solid var(--line); border-radius:999px; padding:3px 7px; color:#475467; background:#fbfcfe; font-size:11px; }
    .requirements { margin-top:10px; display:none; border:1px solid var(--line); border-radius:8px; padding:11px; background:#fff; }
    .requirements.visible { display:block; }
    .requirements summary { cursor:pointer; font-weight:600; font-size:12px; }
    .requirements-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px; }
    .requirements label { color:var(--muted); font-size:11px; }
    .requirements input { width:100%; height:34px; margin-top:4px; padding:6px 8px; border:1px solid var(--line); border-radius:5px; }
    .requirements .wide { grid-column:1/-1; }
    .requirements button { margin-top:10px; width:100%; }
    .error { display:none; margin-top:8px; padding:8px 10px; border:1px solid #f3c6c2; border-radius:6px; color:var(--bad); background:var(--bad-soft); font-size:12px; }
    .busy::after { content:""; display:block; width:100%; height:3px; margin-top:8px; background:linear-gradient(90deg,var(--accent) 0 35%,#dfe5ed 35% 100%); animation:busy 1.1s infinite linear; }
    @keyframes busy { from { background-position:-200px 0; } to { background-position:200px 0; } }
    @media(min-width:1181px) { body.history-collapsed .layout { grid-template-columns:48px minmax(550px,650px) minmax(540px,1fr); } }
    @media(min-width:681px) { body.history-collapsed .history { padding:14px 6px; overflow:hidden; } body.history-collapsed .history .pane-title { justify-content:center; margin:0; } body.history-collapsed .history .pane-title h2, body.history-collapsed .history .new-button, body.history-collapsed .history-tools, body.history-collapsed .history-group, body.history-collapsed .history-list { display:none; } body.history-collapsed .pane-actions { width:100%; justify-content:center; } }
    @media(max-width:1180px) { .layout { grid-template-columns:190px minmax(340px,430px) minmax(460px,1fr); } }
    @media(max-width:1180px) and (min-width:941px) { body.history-collapsed .layout { grid-template-columns:48px minmax(482px,572px) minmax(460px,1fr); } }
    @media(max-width:940px) and (min-width:681px) { .layout { height:auto; overflow:visible; grid-template-columns:220px minmax(0,1fr); } .workspace { grid-column:1/-1; border-top:1px solid var(--line); } .workspace-empty { min-height:360px; } body.history-collapsed .layout { grid-template-columns:48px minmax(0,1fr); } body.history-collapsed .workspace { grid-column:1/-1; } }
    @media(max-width:680px) { header { height:auto; min-height:60px; padding:10px 14px; flex-wrap:wrap; } .brand { flex-direction:column; gap:1px; } nav { display:none; } .history-toggle { display:block; } .sidebar-toggle { display:none; } .mobile-modes { order:3; display:grid; grid-template-columns:1fr 1fr; width:100%; padding:3px; border:1px solid var(--line); border-radius:6px; background:#f4f6f9; } .mobile-mode { min-height:32px; border:0; background:transparent; } .mobile-mode.active { color:var(--accent); background:#fff; box-shadow:0 1px 3px rgba(23,32,51,.09); } .layout { display:block; height:calc(100vh - 98px); min-height:0; } .history { display:block; visibility:hidden; position:fixed; z-index:20; top:98px; bottom:0; left:0; width:min(300px,86vw); transform:translateX(-105%); transition:transform .18s ease,visibility 0s linear .18s; box-shadow:8px 0 24px rgba(23,32,51,.14); } body.history-open .history { visibility:visible; transform:translateX(0); transition-delay:0s; } .conversation { height:100%; min-height:0; border-right:0; } body.mobile-view-chat .workspace { display:none; } body.mobile-view-results .conversation { display:none; } body.mobile-view-results .workspace { display:block; height:100%; } .messages { min-height:0; } .decision { flex-direction:column; } .decision button { width:100%; } .price-facts,.price-options { grid-template-columns:1fr; } .price-fact { border-right:0; border-bottom:1px solid #ead8b7; } .price-fact:last-child { border-bottom:0; } .price-option p { min-height:0; } .metrics { grid-template-columns:repeat(2,minmax(0,1fr)); } .metric { border-bottom:1px solid var(--line); } .grid { grid-template-columns:1fr; } .card.wide { grid-column:auto; } .work-body { padding:16px; } }
  </style>
</head>
<body class="mobile-view-chat">
  <header>
    <div class="brand"><strong>EcomPilot</strong><span>对话式电商运营工作台</span></div>
    <button class="history-toggle" id="historyToggle" type="button" title="打开历史会话" aria-label="打开历史会话">☰</button>
    <nav><a id="opsLink" href="/ops">运维监控</a><a id="traceLink" href="/traces">技术证据</a><a href="/seller-center">模拟店铺</a></nav>
    <div class="mobile-modes" aria-label="移动端视图"><button class="mobile-mode active" data-mobile-mode="chat" type="button">对话</button><button class="mobile-mode" data-mobile-mode="results" type="button">结果</button></div>
  </header>
  <main class="layout">
    <aside class="history">
      <div class="pane-title"><h2>会话</h2><div class="pane-actions"><button class="sidebar-toggle" id="sidebarToggle" type="button" title="收起会话列表" aria-label="收起会话列表" aria-expanded="true">‹</button><button class="new-button" id="newConversation" type="button" title="新建会话">+</button></div></div>
      <div class="history-tools">
        <input id="historySearch" type="search" placeholder="搜索会话内容" />
        <div class="history-filter-row"><select id="productFilter"><option value="">全部商品</option></select><select id="approvalFilter"><option value="all">全部状态</option><option value="pending">待我确认</option></select></div>
      </div>
      <div class="history-group">最近会话</div>
      <div class="history-list" id="historyList"><div class="history-empty">正在加载会话</div></div>
    </aside>
    <section class="conversation">
      <div class="conversation-head"><div><h1>运营助手</h1><p>用自然语言描述你希望完成的业务任务</p></div></div>
      <div class="messages" id="messages">
        <div class="message assistant">
          <div class="bubble">你好，我可以协助商品上新、市场调研和已上架商品分析。
            <div class="quick-actions"><button class="example" data-example="listing" type="button">上架一款成本95元、售价300元、库存800件的游戏无线耳机</button><button class="example" data-example="market" type="button">调研游戏无线耳机的价格区间和用户关注点</button><button class="example" data-example="analytics" type="button">查看我之前上架的无线耳机最近30天销售表现</button></div>
          </div>
        </div>
      </div>
      <div class="composer" id="composer">
        <textarea id="messageInput" placeholder="输入你的运营需求"></textarea>
        <details class="requirements" id="requirementsEditor">
          <summary>系统理解的需求</summary>
          <div class="requirements-grid">
            <label>商品类别<input id="reqCategory" /></label><label>目标人群<input id="reqAudience" /></label>
            <label>成本<input id="reqCost" type="number" min="0" step="0.01" /></label><label>售价<input id="reqPrice" type="number" min="0.01" step="0.01" /></label>
            <label>库存<input id="reqInventory" type="number" min="0" step="1" /></label><label>最低毛利率（%）<input id="reqMargin" type="number" min="0" max="99" step="0.1" /></label>
            <label class="wide">已确认产品功能<input id="reqFeatures" /></label>
          </div>
          <button id="applyRequirements" type="button">按修正后的需求重新生成</button>
        </details>
        <div class="error" id="errorBar"></div>
        <div class="composer-actions">
          <div class="service" aria-live="polite"><span id="llmService">模型服务检查中</span><span id="browserService">店铺服务检查中</span></div>
          <button class="primary" id="sendButton" type="button" disabled>发送</button>
        </div>
      </div>
    </section>
    <section class="workspace">
      <div class="workspace-empty" id="workspaceEmpty"><div><strong>等待你的任务</strong><span>方案、定价和风险检查会显示在这里</span></div></div>
      <div class="workspace-content" id="workspaceContent">
        <div class="decision" id="decision"><div><h2 id="decisionTitle">-</h2><p id="decisionText">-</p><div class="decision-meta" id="decisionMeta"></div><div class="batch-selection" id="batchSelection"></div></div><div class="batch-actions"><button class="execute" id="executeButton" type="button" style="display:none">确认并同步到模拟店铺</button><button class="retry-batch" id="retryButton" type="button" style="display:none">重试失败商品</button></div></div>
        <section class="price-confirmation" id="priceConfirmation" aria-live="polite">
          <h2>确认售价后继续</h2>
          <p id="priceConfirmationSummary">目标售价明显偏离当前核心可比市场，请选择下一步。</p>
          <div class="price-facts">
            <div class="price-fact"><small>你的目标售价</small><strong id="priceTarget">-</strong></div>
            <div class="price-fact"><small>核心参考价</small><strong id="priceReference">-</strong></div>
            <div class="price-fact"><small>可接受区间</small><strong id="priceAcceptance">-</strong></div>
            <div class="price-fact"><small>偏离程度</small><strong id="priceDeviation">-</strong></div>
          </div>
          <div class="price-options">
            <article class="price-option"><h3>采用建议价格</h3><p id="adoptPriceDescription">按建议区间继续生成方案。</p><button class="recommended" id="adoptPriceButton" type="button">采用建议价格</button></article>
            <article class="price-option"><h3>保留原价</h3><p>填写可以核验的品牌、材质或功能依据；仅有“我觉得可以”不算依据。</p><input id="priceEvidence" maxlength="500" placeholder="例如：已确认使用金属腔体和独立游戏芯片" /><button id="keepPriceButton" type="button">提交依据并保留原价</button></article>
            <article class="price-option"><h3>只看市场分析</h3><p>停止本次上新，不生成商品和促销方案，也不会修改店铺。</p><button id="marketOnlyButton" type="button">结束上新并保留分析</button></article>
          </div>
          <div class="price-option-error" id="priceOptionError"></div>
        </section>
        <div class="execution-receipt" id="executionReceipt" role="status" aria-live="polite"></div>
        <div class="metrics" id="businessMetrics">
          <div class="metric"><small>售价</small><strong id="metricPrice">-</strong></div>
          <div class="metric"><small>优惠</small><strong id="metricCoupon">-</strong></div>
          <div class="metric"><small>预计到手价</small><strong id="metricNetPrice">-</strong></div>
          <div class="metric"><small>预计毛利率</small><strong id="metricMargin">-</strong></div>
          <div class="metric"><small>计划投入</small><strong id="metricUnits">-</strong></div>
        </div>
        <div class="tabs" id="workspaceTabs">
          <button class="tab" data-panel="marketPanel" data-business-panel="market" type="button">市场调研</button>
          <button class="tab active" data-panel="listingPanel" data-business-panel="listing" type="button">商品方案</button>
          <button class="tab" data-panel="strategyPanel" data-business-panel="strategy" type="button">定价促销</button>
          <button class="tab" data-panel="reviewPanel" data-business-panel="review" type="button">风险检查</button>
          <button class="tab" data-panel="executionPanel" data-business-panel="execution" type="button">执行结果</button>
          <button class="tab" data-panel="analyticsPanel" data-business-panel="analytics" type="button">销售表现</button>
          <button class="tab" data-panel="productPanel" data-business-panel="product" type="button">商品档案</button>
          <button class="tab" data-panel="timelinePanel" data-business-panel="timeline" type="button">商品时间线</button>
        </div>
        <div class="work-body" id="workspaceBody">
          <div class="tab-panel" id="marketPanel"><h2 class="section-title">市场调研结果</h2><div class="grid"><article class="card"><h3>核心可比商品</h3><p id="marketCoreLayer">尚未执行</p></article><article class="card"><h3>相邻档次商品</h3><p id="marketAdjacentLayer">尚未执行</p></article><article class="card"><h3>全市场范围</h3><p id="marketFullLayer">尚未执行</p></article><article class="card"><h3>样本清洗与可信度</h3><p id="marketSample">尚未执行</p></article><article class="card"><h3>高频卖点</h3><div class="tags" id="marketHighlights"><span class="tag">尚未执行</span></div></article><article class="card"><h3>用户关注</h3><div class="tags" id="marketPainPoints"><span class="tag">尚未执行</span></div></article></div></div>
          <div class="tab-panel active" id="listingPanel"><h2 class="section-title">商品页面方案</h2><div class="grid"><article class="card wide"><h3>商品标题</h3><p id="listingTitle">尚未执行</p></article><article class="card"><h3>核心卖点</h3><div id="listingBullets"><p>尚未执行</p></div></article><article class="card"><h3>搜索关键词</h3><div class="tags" id="listingKeywords"><span class="tag">尚未执行</span></div></article><article class="card wide"><h3>表述与参数确认</h3><div id="complianceNotes"><p>尚未执行</p></div></article></div></div>
          <div class="tab-panel" id="strategyPanel"><h2 class="section-title">定价与促销</h2><div class="grid"><article class="card wide"><h3>首月方案</h3><p id="launchPlan">尚未执行</p></article><article class="card"><h3>库存安排</h3><p id="inventoryPlan">尚未执行</p></article><article class="card"><h3>市场参考</h3><p id="marketReference">尚未执行</p></article><article class="card wide"><h3>本次决策参考</h3><div id="strategyEvidence"><p>尚未执行</p></div></article></div></div>
          <div class="tab-panel" id="reviewPanel"><h2 class="section-title">风险与修改建议</h2><div class="grid"><article class="card wide"><div id="riskContent"><p>尚未执行</p></div></article></div></div>
          <div class="tab-panel" id="executionPanel"><h2 class="section-title">店铺同步结果</h2><div class="grid"><article class="card wide"><div id="syncContent"><p>尚未执行</p></div></article></div></div>
          <div class="tab-panel" id="analyticsPanel"><h2 class="section-title">商品销售表现</h2><div class="grid"><article class="card"><h3>查询期间</h3><p id="analyticsPeriod">尚未查询</p></article><article class="card"><h3>数据来源</h3><p id="analyticsSource">尚未查询</p></article><article class="card"><h3>销量与销售额</h3><p id="analyticsSales">尚未查询</p></article><article class="card"><h3>访问与转化</h3><p id="analyticsConversion">尚未查询</p></article><article class="card"><h3>库存</h3><p id="analyticsInventory">尚未查询</p></article><article class="card"><h3>调用的证据工具</h3><div id="analyticsTools"><p>尚未查询</p></div></article><article class="card wide"><h3>趋势与活动补充</h3><div id="analyticsDetails"><p>本次未查询补充证据。</p></div></article></div></div>
          <div class="tab-panel" id="productPanel"><h2 class="section-title">商品档案</h2><div class="grid"><article class="card wide"><h3>身份信息</h3><div id="productIdentity"><p>尚未查询</p></div></article><article class="card"><h3>店铺状态</h3><p id="productStoreState">尚未查询</p></article><article class="card"><h3>来源任务</h3><p id="productSourceTask">尚未查询</p></article></div></div>
          <div class="tab-panel" id="timelinePanel"><h2 class="section-title">商品时间线</h2><div class="grid"><article class="card wide"><div id="productTimeline"><p>尚未查询</p></div></article></div></div>
        </div>
      </div>
    </section>
  </main>
  <script>
    const EXAMPLE = '我要上架一款成本 95 元的无线耳机，目标售价 300 元，主要面向游戏爱好者，库存 800 件，毛利率不能低于 40%。已确认的产品功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。已确认的产品形态：未确认。运营目标：面向爱打游戏的群体，主打性价比高、游戏延迟低。';
    const MARKET_EXAMPLE = '请调研游戏无线耳机的整体价格区间、常见卖点和用户关注的问题，只做分析，不修改店铺。';
    const ANALYTICS_EXAMPLE = '请查看我之前上架的无线耳机最近30天的销量、销售额、转化率和库存变化。';
    let currentResponse = null;
    let currentConversationId = null;
    let linkedReady = false;
    let activeEventSource = null;
    let liveMessage = null;
    let activeBatchRuntimeJobId = null;
    let batchExecutionPending = false;
    let batchRecoveryEpoch = 0;
    const appliedBatchRuntimeJobs = new Set();
    const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const panel = id => currentResponse?.panels?.find(item => item.panel_id === id) || {status:'not_run',data:{},summary:'尚未执行。'};
    const listHtml = items => Array.isArray(items) && items.length ? `<ul>${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : '<p>暂无</p>';
    const tagsHtml = items => Array.isArray(items) && items.length ? items.map(item => `<span class="tag">${esc(item)}</span>`).join('') : '<span class="tag">暂无</span>';
    const money = value => value == null ? '-' : `${Number(value).toFixed(Number(value) % 1 ? 2 : 0)} 元`;
    const percent = value => value == null ? '-' : `${(Number(value) * 100).toFixed(2)}%`;
    const moneyBand = value => Array.isArray(value)
      && value.length === 2
      && Number.isFinite(Number(value[0]))
      && Number.isFinite(Number(value[1]))
      ? `${money(value[0])} 至 ${money(value[1])}`
      : '暂无可靠区间';

    function addMessage(role, text, response=null) {
      const wrapper = document.createElement('div');
      wrapper.className = `message ${role}`;
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = text;
      if (response && role === 'assistant') {
        const summary = document.createElement('div');
        summary.className = 'action-summary';
        response.action_summary.steps.forEach(step => {
          const row = document.createElement('div');
          row.className = `action-row ${step.status}`;
          row.innerHTML = `<span class="action-mark">${step.status === 'completed' ? '✓' : step.status === 'failed' ? '×' : '·'}</span><span>${esc(step.detail)}</span>`;
          summary.appendChild(row);
        });
        bubble.appendChild(summary);
        if (response.approval_required) {
          const approve = document.createElement('button');
          approve.className = 'message-approval';
          approve.type = 'button';
          approve.textContent = '确认当前方案并同步店铺';
          approve.addEventListener('click', approveCurrent);
          bubble.appendChild(approve);
        }
        if (response.price_confirmation) {
          const inspect = document.createElement('button');
          inspect.className = 'message-approval';
          inspect.type = 'button';
          inspect.textContent = '查看价格依据并选择下一步';
          inspect.addEventListener('click', () => {
            document.body.classList.remove('mobile-view-chat');
            document.body.classList.add('mobile-view-results');
            document.querySelectorAll('[data-mobile-mode]').forEach(item => item.classList.toggle('active', item.dataset.mobileMode === 'results'));
            document.getElementById('priceConfirmation').scrollIntoView({behavior:'smooth',block:'start'});
          });
          bubble.appendChild(inspect);
        }
      }
      wrapper.appendChild(bubble);
      if (response) {
        const meta = document.createElement('div');
        meta.className = 'message-meta';
        const usage = response.model_usage;
        meta.textContent = usage.mode === 'real_model'
          ? `本次实际模型调用 ${usage.actual_call_count} 次 · 工具调用 ${response.action_summary.tool_call_count} 次`
          : usage.mode === 'test_stub'
            ? `本次为测试桩调用 ${usage.stub_call_count} 次 · 未调用真实模型`
            : `本次模型调用 0 次 · 工具调用 ${response.action_summary.tool_call_count} 次`;
        wrapper.appendChild(meta);
      }
      document.getElementById('messages').appendChild(wrapper);
      wrapper.scrollIntoView({behavior:'smooth',block:'end'});
    }

    function requestId() {
      return globalThis.crypto?.randomUUID ? `req_${crypto.randomUUID().replaceAll('-', '')}` : `req_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    }

    async function readJsonResponse(response) {
      const text = await response.text();
      if (!text.trim()) return {};
      try {
        return JSON.parse(text);
      } catch (_error) {
        return {
          detail: response.ok
            ? '服务返回了无法识别的结果，请刷新任务状态。'
            : `服务暂时无法完成请求（HTTP ${response.status}）。`,
          response_preview: text.slice(0, 240)
        };
      }
    }

    function resetConversation() {
      invalidateBatchRecovery();
      activeEventSource?.close();
      activeEventSource = null;
      liveMessage?.remove();
      liveMessage = null;
      currentResponse = null;
      currentConversationId = null;
      history.replaceState({}, '', '/user');
      document.getElementById('messages').innerHTML = '';
      addWelcomeMessage();
      const empty = document.getElementById('workspaceEmpty');
      empty.style.display = 'grid';
      empty.innerHTML = '<div><strong>等待你的任务</strong><span>方案、定价和风险检查会显示在这里</span></div>';
      document.getElementById('workspaceContent').classList.remove('visible');
      hideExecutionReceipt();
      document.getElementById('requirementsEditor').classList.remove('visible');
      document.getElementById('messageInput').value = '';
      renderHistorySelection();
      document.body.classList.remove('history-open');
    }

    function addWelcomeMessage() {
      const wrapper = document.createElement('div');
      wrapper.className = 'message assistant';
      wrapper.innerHTML = `<div class="bubble">你好，我可以协助商品上新、市场调研和已上架商品分析。<div class="quick-actions"><button class="example" data-example="listing" type="button">上架一款成本95元、售价300元、库存800件的游戏无线耳机</button><button class="example" data-example="market" type="button">调研游戏无线耳机的价格区间和用户关注点</button><button class="example" data-example="analytics" type="button">查看我之前上架的无线耳机最近30天销售表现</button></div></div>`;
      bindExampleButtons(wrapper);
      document.getElementById('messages').appendChild(wrapper);
    }

    function useExample(kind='listing') {
      document.getElementById('messageInput').value = ({listing:EXAMPLE,market:MARKET_EXAMPLE,analytics:ANALYTICS_EXAMPLE})[kind] || EXAMPLE;
      document.getElementById('messageInput').focus();
    }

    function bindExampleButtons(root=document) {
      root.querySelectorAll('[data-example]').forEach(button => button.addEventListener('click', () => useExample(button.dataset.example)));
    }

    async function sendMessage(message) {
      if (!message.trim() || !linkedReady) return;
      invalidateBatchRecovery();
      hideError();
      setBusy(true);
      addMessage('user', message.trim());
      showWorkspaceProgress();
      try {
        const response = await fetch('/api/copilot/messages/dispatch', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:message.trim(),conversation_id:currentConversationId,client_request_id:requestId()})});
        const payload = await readJsonResponse(response);
        if (!response.ok) throw new Error(readError(payload, '任务没有完成'));
        currentConversationId = payload.conversation_id;
        history.replaceState({}, '', `/user?conversation_id=${encodeURIComponent(currentConversationId)}`);
        document.getElementById('messageInput').value = '';
        startEventStream(payload.events_url);
        await loadConversations();
      } catch (error) {
        addMessage('assistant', error.message);
        showWorkspaceFailure(error.message);
        setBusy(false);
      }
    }

    function startEventStream(url) {
      if (activeEventSource) activeEventSource.close();
      const wrapper = document.createElement('div');
      wrapper.className = 'message assistant';
      wrapper.innerHTML = '<div class="bubble"><div class="live-status"><div class="live-row running"><span class="live-dot"></span><span>正在连接任务进度</span></div></div></div>';
      document.getElementById('messages').appendChild(wrapper);
      liveMessage = wrapper;
      const source = new EventSource(url);
      activeEventSource = source;
      const eventNames = ['request_received','intent_recognized','route_planned','agent_started','agent_completed','model_completed','tool_completed','review_revised','stage_failed','approval_waiting','execution_completed','response_ready','stream_failed'];
      eventNames.forEach(name => source.addEventListener(name, event => {
        if (activeEventSource !== source || liveMessage !== wrapper) return;
        handleProgressEvent(name, JSON.parse(event.data), source, wrapper);
      }));
      source.onerror = () => {
        if (activeEventSource !== source || source.readyState === EventSource.CLOSED) return;
        updateLiveRow('正在恢复进度连接', 'running', wrapper);
      };
    }

    function updateLiveRow(text, status='completed', messageElement=liveMessage) {
      if (!messageElement) return;
      const list = messageElement.querySelector('.live-status');
      const row = document.createElement('div');
      row.className = `live-row ${status}`;
      row.innerHTML = `<span class="live-dot"></span><span>${esc(text)}</span>`;
      const running = list.querySelector('.running');
      if (running) running.remove();
      list.appendChild(row);
      messageElement.scrollIntoView({behavior:'smooth',block:'end'});
    }

    async function handleProgressEvent(name, event, source, messageElement) {
      if (activeEventSource !== source || liveMessage !== messageElement) return;
      if (name === 'response_ready') {
        source.close();
        activeEventSource = null;
        messageElement.remove();
        liveMessage = null;
        const response = event.payload.response;
        renderResponse(response);
        addMessage('assistant', response.assistant_message, response);
        await loadConversations();
        setBusy(false);
        return;
      }
      if (name === 'stream_failed') {
        source.close();
        activeEventSource = null;
        messageElement.remove();
        liveMessage = null;
        addMessage('assistant', event.detail);
        showWorkspaceFailure(event.detail);
        setBusy(false);
        await loadConversations();
        return;
      }
      updateLiveRow(event.detail || event.title, event.status, messageElement);
    }

    function showWorkspaceProgress() {
      currentResponse = null;
      hideExecutionReceipt();
      document.getElementById('workspaceContent').classList.remove('visible');
      const empty = document.getElementById('workspaceEmpty');
      empty.style.display = 'grid';
      empty.innerHTML = '<div><strong>正在处理本次任务</strong><span>系统正在理解需求并准备本轮结果</span></div>';
    }

    function showWorkspaceFailure(message) {
      currentResponse = null;
      hideExecutionReceipt();
      document.getElementById('workspaceContent').classList.remove('visible');
      const empty = document.getElementById('workspaceEmpty');
      empty.style.display = 'grid';
      empty.innerHTML = `<div><strong>本次任务未完成</strong><span>${esc(message)}</span></div>`;
    }

    async function approveCurrent() {
      const batch = currentBatch();
      if (!currentResponse?.approval_required && !batch) return;
      setBusy(true);
      try {
        let payload;
        if (batch) {
          const selected = [...document.querySelectorAll('[data-batch-item]:checked')].map(input => input.dataset.batchItem);
          if (!selected.length) throw new Error('请至少选择一个要同步的商品。');
          const versions = Object.fromEntries(
            batch.items
              .filter(item => selected.includes(item.item_id) && item.checkpoint_version != null)
              .map(item => [item.item_id, item.checkpoint_version])
          );
          payload = await dispatchBatchExecution(batch.batch_job_id, 'approve', selected, versions);
        } else {
          const response = await fetch(`/api/copilot/tasks/${encodeURIComponent(currentResponse.task_id)}/approve`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_checkpoint_version:null,execution_plan_hash:currentResponse.execution_plan_hash,reason:'用户确认当前方案'})});
          payload = await readJsonResponse(response);
          if (!response.ok) throw new Error(readError(payload, '店铺同步没有完成'));
        }
        if (batch) {
          if (!payload) return;
          applyBatchExecution(payload);
          addMessage('assistant', payload.assistant_message);
        } else {
          renderResponse(payload);
          addMessage('assistant', payload.assistant_message, payload);
        }
        await loadConversations();
      } catch (error) { showError(error.message); }
      finally { setBatchExecutionPending(false); setBusy(false); }
    }

    async function confirmPrice(action) {
      const prompt = currentResponse?.price_confirmation;
      if (!prompt || !currentResponse?.task_id) return;
      const error = document.getElementById('priceOptionError');
      error.style.display = 'none';
      const evidence = document.getElementById('priceEvidence').value.trim();
      if (action === 'keep_original_with_evidence' && evidence.length < 4) {
        error.textContent = '请填写至少 4 个字的可核验差异化依据。';
        error.style.display = 'block';
        document.getElementById('priceEvidence').focus();
        return;
      }
      const option = prompt.options.find(item => item.action === action) || {};
      const userText = action === 'adopt_suggested_price'
        ? `采用建议价格${option.suggested_price ? ` ${option.suggested_price} 元` : ''}`
        : action === 'keep_original_with_evidence'
          ? `保留原价，因为${evidence}`
          : '只看市场分析，不继续上架';
      addMessage('user', userText);
      setBusy(true);
      document.querySelectorAll('#priceConfirmation button').forEach(button => button.disabled = true);
      try {
        const response = await fetch(`/api/copilot/tasks/${encodeURIComponent(currentResponse.task_id)}/price-confirmation`, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            action,
            selected_price:action === 'adopt_suggested_price' ? option.suggested_price || null : null,
            evidence:action === 'keep_original_with_evidence' ? evidence : null,
            expected_checkpoint_version:prompt.checkpoint_version,
            client_request_id:requestId(),
          }),
        });
        const payload = await readJsonResponse(response);
        if (!response.ok) throw new Error(readError(payload, '价格确认没有完成'));
        renderResponse(payload);
        addMessage('assistant', payload.assistant_message, payload);
        await loadConversations();
      } catch (failure) {
        error.textContent = failure.message;
        error.style.display = 'block';
      } finally {
        document.querySelectorAll('#priceConfirmation button').forEach(button => button.disabled = false);
        setBusy(false);
      }
    }

    async function retryBatchItems() {
      const batch = currentBatch();
      if (!batch) return;
      const selected = [...document.querySelectorAll('[data-batch-retry]:checked')].map(input => input.dataset.batchRetry);
      if (!selected.length) return showError('请至少选择一个需要重试的商品。');
      setBusy(true);
      try {
        const payload = await dispatchBatchExecution(batch.batch_job_id, 'retry', selected, {});
        if (!payload) return;
        applyBatchExecution(payload);
        addMessage('assistant', payload.assistant_message);
        await loadConversations();
      } catch (error) { showError(error.message); }
      finally { setBatchExecutionPending(false); setBusy(false); }
    }

    async function dispatchBatchExecution(batchJobId, operation, itemIds, versions) {
      const clientRequestId = globalThis.crypto?.randomUUID
        ? `batch_${crypto.randomUUID()}`
        : `batch_${Date.now()}_${Math.random().toString(16).slice(2)}`;
      const response = await fetch(`/api/copilot/batches/${encodeURIComponent(batchJobId)}/dispatch`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          operation,
          item_ids:itemIds,
          expected_checkpoint_versions:versions,
          client_request_id:clientRequestId
        })
      });
      const dispatch = await readJsonResponse(response);
      if (!response.ok) throw new Error(readError(dispatch, '批次任务没有进入执行队列'));
      return waitForBatchExecution(
        dispatch.runtime_job_id,
        dispatch.status_url,
        batchJobId,
      );
    }

    async function waitForBatchExecution(runtimeJobId, statusUrl, batchJobId) {
      const epoch = batchRecoveryEpoch;
      activeBatchRuntimeJobId = runtimeJobId;
      setBatchExecutionPending(true);
      showExecutionReceipt('running', '后台执行任务已安全保存。页面刷新后会自动恢复进度。', runtimeJobId);
      document.getElementById('decisionText').textContent = '正在等待店铺同步服务处理。';
      for (let attempt = 0; attempt < 360; attempt += 1) {
        if (
          epoch !== batchRecoveryEpoch
          || activeBatchRuntimeJobId !== runtimeJobId
          || currentBatch()?.batch_job_id !== batchJobId
        ) return null;
        await new Promise(resolve => setTimeout(resolve, 500));
        const statusResponse = await fetch(statusUrl);
        const status = await readJsonResponse(statusResponse);
        if (!statusResponse.ok) throw new Error(readError(status, '批次执行状态查询失败'));
        if (!status.is_latest) return null;
        if (status.status === 'completed' && status.result) {
          appliedBatchRuntimeJobs.add(runtimeJobId);
          showExecutionReceipt('completed', '店铺同步已完成，执行回执已经保存。', runtimeJobId);
          return status.result;
        }
        if (['failed','dead'].includes(status.status)) {
          showExecutionReceipt('failed', '店铺同步任务已停止，详细原因可在运维监控中查看。', runtimeJobId);
          throw new Error(status.error || '批次执行已经停止，请在运维监控中查看失败记录。');
        }
        document.getElementById('decisionText').textContent = status.status === 'leased'
          ? `店铺同步服务正在处理（第 ${status.attempts}/${status.max_attempts} 次尝试）。`
          : '任务仍在持久化队列中等待执行。';
      }
      throw new Error('批次执行仍在后台进行，请稍后从当前会话查看最终结果。');
    }

    async function resumeLatestBatchExecution(batchJobId) {
      const epoch = batchRecoveryEpoch;
      try {
        const response = await fetch(`/api/copilot/batches/${encodeURIComponent(batchJobId)}/executions/latest`);
        const status = await readJsonResponse(response);
        if (!response.ok) throw new Error(readError(status, '后台执行回执恢复失败'));
        if (
          !status
          || !status.is_latest
          || epoch !== batchRecoveryEpoch
          || currentBatch()?.batch_job_id !== batchJobId
          || appliedBatchRuntimeJobs.has(status.runtime_job_id)
        ) return;
        if (status.status === 'completed' && status.result) {
          appliedBatchRuntimeJobs.add(status.runtime_job_id);
          applyBatchExecution(status.result);
          return;
        }
        if (['failed','dead'].includes(status.status)) {
          showError(status.error || '最近一次批次执行已经停止，请查看运维监控。');
          return;
        }
        if (activeBatchRuntimeJobId === status.runtime_job_id) return;
        const result = await waitForBatchExecution(
          status.runtime_job_id,
          `/api/copilot/batch-executions/${encodeURIComponent(status.runtime_job_id)}`,
          batchJobId,
        );
        if (result && currentBatch()?.batch_job_id === batchJobId) {
          applyBatchExecution(result);
        }
      } catch (error) {
        if (epoch === batchRecoveryEpoch && currentBatch()?.batch_job_id === batchJobId) {
          showError(error.message);
        }
      } finally {
        if (epoch === batchRecoveryEpoch) setBatchExecutionPending(false);
      }
    }

    function setBatchExecutionPending(pending) {
      batchExecutionPending = pending;
      updateBatchButton();
    }

    function showExecutionReceipt(status, message, runtimeJobId='') {
      const receipt = document.getElementById('executionReceipt');
      receipt.className = `execution-receipt visible ${status}`;
      receipt.textContent = message;
      receipt.title = runtimeJobId ? `执行回执：${runtimeJobId}` : '';
    }

    function hideExecutionReceipt() {
      const receipt = document.getElementById('executionReceipt');
      receipt.className = 'execution-receipt';
      receipt.textContent = '';
      receipt.title = '';
    }

    function invalidateBatchRecovery() {
      batchRecoveryEpoch += 1;
      activeBatchRuntimeJobId = null;
      batchExecutionPending = false;
    }

    function currentBatch() {
      const data = currentResponse?.panels?.find(item => item.panel_id === 'requirements')?.data;
      return data?.batch_job_id && Array.isArray(data.items)
        ? {batch_job_id:data.batch_job_id,items:data.items}
        : null;
    }

    function selectedBatchCount() {
      return document.querySelectorAll('[data-batch-item]:checked').length;
    }

    function selectedRetryCount() {
      return document.querySelectorAll('[data-batch-retry]:checked').length;
    }

    function updateBatchButton() {
      const execute = document.getElementById('executeButton');
      const count = selectedBatchCount();
      execute.textContent = count ? `确认并同步 ${count} 个商品` : '请选择要同步的商品';
      execute.disabled = !count || batchExecutionPending;
      const retry = document.getElementById('retryButton');
      const retryCount = selectedRetryCount();
      retry.textContent = retryCount ? `重试 ${retryCount} 个失败商品` : '请选择要重试的商品';
      retry.disabled = !retryCount || batchExecutionPending;
    }

    function renderBatchSelection(response) {
      const container = document.getElementById('batchSelection');
      const data = response.panels?.find(item => item.panel_id === 'requirements')?.data;
      const pending = (data?.items || []).filter(item => item.status === 'awaiting_approval');
      const retryable = (data?.items || []).filter(item => ['failed','needs_attention'].includes(item.status) && (item.execution_attempts || 0) < 3);
      if (!data?.batch_job_id || (!pending.length && !retryable.length)) {
        container.className = 'batch-selection';
        container.innerHTML = '';
        return {hasApproval:false,hasRetry:false};
      }
      container.className = 'batch-selection visible';
      container.innerHTML = `${pending.length ? `<strong>选择需要同步到模拟店铺的商品</strong>${pending.map(item => `<label class="batch-option"><input type="checkbox" checked data-batch-item="${esc(item.item_id)}"><span>${esc(item.label)} · 独立任务 ${esc(item.task_id)}</span></label>`).join('')}` : ''}${retryable.length ? `<strong>选择需要恢复的失败商品</strong>${retryable.map(item => `<label class="batch-option"><input type="checkbox" checked data-batch-retry="${esc(item.item_id)}"><span>${esc(item.label)} · 已尝试 ${item.execution_attempts || 0}/3 次</span></label>`).join('')}` : ''}`;
      container.querySelectorAll('input').forEach(input => input.addEventListener('change', updateBatchButton));
      return {hasApproval:pending.length > 0,hasRetry:retryable.length > 0};
    }

    function applyBatchExecution(report) {
      const data = currentResponse.panels.find(item => item.panel_id === 'requirements').data;
      const outcomes = new Map(report.items.map(item => [item.item_id,item]));
      data.items = data.items.map(item => {
        const outcome = outcomes.get(item.item_id);
        return outcome ? {...item,status:outcome.status,error_code:outcome.error_code,execution_attempts:outcome.execution_attempts} : item;
      });
      data.batch_status = report.status;
      currentResponse.assistant_message = report.assistant_message;
      currentResponse.store_modified = report.executed_count > 0;
      currentResponse.outcome = report.status === 'failed'
        ? 'technical_failed'
        : report.status === 'awaiting_approval'
          ? 'awaiting_approval'
          : 'completed';
      currentResponse.panels = currentResponse.panels.filter(item => item.panel_id !== 'execution');
      currentResponse.panels.push({
        panel_id:'execution',
        title:'批次店铺同步结果',
        status:report.status === 'completed' ? 'completed' : report.status === 'failed' ? 'failed' : 'ready',
        summary:report.assistant_message,
        data:{batch_execution:report},
        source_agents:['batch_execution_service']
      });
      renderResponse(currentResponse);
    }

    function renderResponse(response) {
      currentResponse = response;
      currentConversationId = response.conversation_id || currentConversationId;
      hideError();
      document.getElementById('workspaceEmpty').style.display = 'none';
      document.getElementById('workspaceContent').classList.add('visible');
      const recoverableBatch = currentBatch();
      const executionReceiptData = panel('execution').data?.batch_execution;
      if (executionReceiptData) showExecutionReceipt(
        executionReceiptData.status === 'completed' ? 'completed' : executionReceiptData.status === 'failed' ? 'failed' : 'running',
        executionReceiptData.status === 'completed' ? '店铺同步已完成，执行回执已经保存。' : executionReceiptData.assistant_message,
      );
      else if (!recoverableBatch) hideExecutionReceipt();
      if (recoverableBatch) void resumeLatestBatchExecution(recoverableBatch.batch_job_id);
      document.getElementById('opsLink').href = response.links.operations || '/ops';
      document.getElementById('traceLink').href = response.links.trace || '/traces';
      const decision = document.getElementById('decision');
      decision.className = `decision ${response.outcome}`;
      document.getElementById('decisionTitle').textContent = outcomeTitle(response.outcome);
      document.getElementById('decisionText').textContent = response.assistant_message;
      const intent = response.intent?.intent || 'unknown';
      const scope = response.data_scope || [];
      const usage = response.model_usage || {};
      const usageText = usage.mode === 'real_model'
        ? `本次真实模型：${usage.actual_call_count || 0} 次`
        : usage.mode === 'test_stub'
          ? `本次测试桩：${usage.stub_call_count || 0} 次`
          : '本次未调用模型';
      document.getElementById('decisionMeta').innerHTML = `<span>识别意图：${esc(intentLabel(intent))}</span><span>${esc(usageText)}</span><span>${response.store_modified ? '已修改店铺' : '未修改店铺'}</span>${scope.length ? `<span>数据范围：${esc(scope.map(scopeLabel).join('、'))}</span>` : '<span>未读取业务数据</span>'}`;
      renderPriceConfirmation(response.price_confirmation);
      const execute = document.getElementById('executeButton');
      const batchActions = renderBatchSelection(response);
      execute.style.display = response.approval_required || batchActions.hasApproval ? 'inline-block' : 'none';
      execute.disabled = !(response.approval_required || batchActions.hasApproval);
      execute.textContent = batchActions.hasApproval ? `确认并同步 ${selectedBatchCount()} 个商品` : '确认并同步到模拟店铺';
      const retry = document.getElementById('retryButton');
      retry.style.display = batchActions.hasRetry ? 'inline-block' : 'none';
      retry.disabled = !batchActions.hasRetry;
      retry.textContent = `重试 ${selectedRetryCount()} 个失败商品`;
      document.querySelectorAll('.message-approval').forEach(button => { if (!response.approval_required) button.remove(); });

      const market = panel('market').data;
      const listing = panel('listing').data;
      const strategy = panel('strategy').data;
      const review = panel('review').data;
      const execution = panel('execution').data;
      const analytics = panel('analytics').data;
      const product = panel('product').data;
      const timeline = panel('timeline').data;
      const panelIds = new Set((response.panels || []).map(item => item.panel_id));
      const hasBusinessOutput = ['market','listing','strategy','review','execution','analytics','product','timeline'].some(id => panelIds.has(id));
      document.getElementById('businessMetrics').style.display = panelIds.has('strategy') ? 'grid' : 'none';
      document.getElementById('workspaceTabs').style.display = hasBusinessOutput ? 'flex' : 'none';
      document.getElementById('workspaceBody').style.display = hasBusinessOutput ? 'block' : 'none';
      const visibleTabs = [...document.querySelectorAll('[data-business-panel]')].filter(button => {
        const visible = panelIds.has(button.dataset.businessPanel);
        button.style.display = visible ? 'inline-flex' : 'none';
        return visible;
      });
      document.querySelectorAll('.tab,.tab-panel').forEach(item => item.classList.remove('active'));
      if (visibleTabs.length) {
        visibleTabs[0].classList.add('active');
        document.getElementById(visibleTabs[0].dataset.panel).classList.add('active');
      }
      const margin = strategy.margin || {};
      const inventory = strategy.inventory_check || {};
      document.getElementById('metricPrice').textContent = money(strategy.price);
      document.getElementById('metricCoupon').textContent = money(strategy.coupon);
      document.getElementById('metricNetPrice').textContent = money(margin.net_price);
      document.getElementById('metricMargin').textContent = percent(margin.margin_rate);
      document.getElementById('metricUnits').textContent = strategy.planned_units == null ? '-' : `${strategy.planned_units} 件`;
      document.getElementById('listingTitle').textContent = listing.title || '尚未执行';
      document.getElementById('listingBullets').innerHTML = listing.title ? listHtml(listing.bullets) : '<p>尚未执行</p>';
      document.getElementById('listingKeywords').innerHTML = listing.title ? tagsHtml(listing.keywords) : '<span class="tag">尚未执行</span>';
      document.getElementById('complianceNotes').innerHTML = listing.title ? listHtml(listing.compliance_notes) : '<p>尚未执行</p>';
      document.getElementById('launchPlan').textContent = strategy.launch_plan || '尚未执行';
      document.getElementById('inventoryPlan').textContent = strategy.launch_plan
        ? inventory.valid === false
          ? `库存不足：现有 ${inventory.inventory ?? 0} 件，计划投入 ${inventory.planned_units ?? 0} 件。`
          : `现有库存 ${inventory.inventory ?? '-'} 件，计划投入 ${inventory.planned_units ?? '-'} 件，预计剩余 ${inventory.remaining ?? '-'} 件。`
        : '尚未执行';
      document.getElementById('marketReference').textContent = market.evidence_status === 'degraded' && !market.sample_size?.competitors
        ? '当前市场样本库没有匹配数据，本次方案仅使用用户确认事实。'
        : market.price_band ? `参考价格区间 ${market.price_band[0]} 至 ${market.price_band[1]} 元，中位价格 ${market.median_price} 元；参考 ${market.sample_size?.competitors ?? 0} 个商品样本。` : '尚未执行';
      const layers = market.market_layers || {};
      const coreLayer = layers.core_comparable || {};
      const adjacentLayer = layers.adjacent_tier || {};
      const fullLayer = layers.full_valid_market || {};
      const cleaning = market.market_statistics || {};
      const assessment = market.price_assessment || {};
      document.getElementById('marketCoreLayer').textContent = coreLayer.sample_count != null
        ? `${moneyBand([coreLayer.price_distribution?.minimum, coreLayer.price_distribution?.maximum])}；参考价 ${money(market.core_reference_price)}；${coreLayer.sample_count} 个商品。`
        : '尚未执行';
      document.getElementById('marketAdjacentLayer').textContent = adjacentLayer.sample_count != null
        ? `${moneyBand([adjacentLayer.price_distribution?.minimum, adjacentLayer.price_distribution?.maximum])}；${adjacentLayer.sample_count} 个商品。`
        : '尚未执行';
      document.getElementById('marketFullLayer').textContent = fullLayer.sample_count != null
        ? `${moneyBand([fullLayer.price_distribution?.minimum, fullLayer.price_distribution?.maximum])}；${fullLayer.sample_count} 个有效商品。`
        : '尚未执行';
      document.getElementById('marketSample').textContent = market.sample_size
        ? `原始商品 ${market.sample_size.raw_competitors ?? 0} 个，排除脏数据 ${market.sample_size.excluded_competitors ?? cleaning.excluded_count ?? 0} 个，核心评论 ${market.sample_size.reviews ?? 0} 条；证据可信度 ${qualityLabel(assessment.evidence_quality || evidenceQualityFromMarket(layers))}。`
        : '尚未执行';
      document.getElementById('marketHighlights').innerHTML = market.high_frequency_highlights ? tagsHtml(market.high_frequency_highlights) : '<span class="tag">尚未执行</span>';
      document.getElementById('marketPainPoints').innerHTML = market.user_pain_points ? tagsHtml(market.user_pain_points) : '<span class="tag">尚未执行</span>';
      const selected = strategy.selected_evidence_tools || [];
      document.getElementById('strategyEvidence').innerHTML = strategy.launch_plan ? (selected.length ? listHtml(selected.map(evidenceLabel)) : '<p>本次未调用额外策略证据。</p>') : '<p>尚未执行</p>';
      document.getElementById('riskContent').innerHTML = response.failure
        ? `<p style="color:var(--bad)"><strong>${esc(response.failure.user_message)}</strong></p>`
        : review.approved_for_execution
          ? `<p style="color:var(--ok)">方案已通过执行前检查。</p>${listHtml(review.review_notes || [])}`
          : '<p>尚未执行</p>';
      const verification = execution.verification || {};
      const batchExecution = execution.batch_execution;
      document.getElementById('syncContent').innerHTML = batchExecution
        ? `<p>${esc(batchExecution.assistant_message)}</p>${listHtml((batchExecution.items || []).map(item => `${item.label}：${item.status === 'completed' ? '同步成功' : item.status === 'failed' ? `同步失败（${item.error_code || '未知原因'}）` : '未执行'}`))}`
        : verification.verified
          ? `<p style="color:var(--ok)">商品、价格、库存和促销信息已回读验证。</p>${renderChecks(verification.checks || {})}`
          : response.approval_required ? '<p>尚未同步。确认方案后才会修改模拟店铺。</p>' : '<p>本次没有修改模拟店铺。</p>';
      const sales = analytics.sales?.metrics || {};
      const analyticsPeriod = analytics.period || {};
      document.getElementById('analyticsPeriod').textContent = analytics.product_id
        ? `${analyticsPeriod.label || '所选期间'}（${analyticsPeriod.start_date} 至 ${analyticsPeriod.end_date}）`
        : '尚未查询';
      document.getElementById('analyticsSource').textContent = analytics.product_id
        ? `${sourceLabel(analytics.source_type)}；更新于 ${formatTime(analytics.source_updated_at)}`
        : '尚未查询';
      document.getElementById('analyticsSales').textContent = analytics.product_id
        ? `售出 ${sales.units_sold ?? 0} 件，销售额 ${money(sales.revenue)}，订单 ${sales.orders ?? 0} 笔。`
        : '尚未查询';
      document.getElementById('analyticsConversion').textContent = analytics.product_id
        ? `曝光 ${sales.impressions ?? 0} 次，点击 ${sales.clicks ?? 0} 次，转化率 ${percent(sales.conversion_rate)}。`
        : '尚未查询';
      document.getElementById('analyticsInventory').textContent = analytics.product_id
        ? `期末库存 ${sales.ending_inventory ?? '-'} 件，退款 ${sales.refunds ?? 0} 件。`
        : '尚未查询';
      document.getElementById('analyticsTools').innerHTML = analytics.product_id
        ? listHtml((analytics.selected_evidence_tools || []).map(analyticsToolLabel))
        : '<p>尚未查询</p>';
      const extraAnalytics = [];
      if (analytics.comparison?.change) extraAnalytics.push(`上一等长周期对比：销量变化 ${analytics.comparison.change.units_sold_rate == null ? '不可计算' : percent(analytics.comparison.change.units_sold_rate)}。`);
      if (analytics.campaigns?.summary) extraAnalytics.push(`活动 ${analytics.campaigns.summary.campaign_count} 场，活动销售额 ${money(analytics.campaigns.summary.revenue)}，综合 ROI ${analytics.campaigns.summary.weighted_roi ?? '-'}。`);
      if (analytics.inventory) extraAnalytics.push(`期间库存从 ${analytics.inventory.starting_inventory} 件变为 ${analytics.inventory.ending_inventory} 件。`);
      document.getElementById('analyticsDetails').innerHTML = analytics.product_id ? listHtml(extraAnalytics) : '<p>尚未查询</p>';
      document.getElementById('productIdentity').innerHTML = product.product_id
        ? `<p><strong>${esc(product.title)}</strong></p><p>商品 ID：${esc(product.product_id)}<br>SKU：${esc(product.sku || '未设置')}<br>类别：${esc(product.category)}<br>状态：${esc(product.status)}</p>`
        : '<p>尚未查询</p>';
      document.getElementById('productStoreState').textContent = product.product_id
        ? `售价 ${product.price ?? '-'} 元，库存 ${product.stock ?? '-'} 件。`
        : '尚未查询';
      document.getElementById('productSourceTask').textContent = product.source_task_id || '尚未查询';
      const events = timeline.events || [];
      document.getElementById('productTimeline').innerHTML = events.length
        ? `<ul>${events.map(event => `<li><strong>${esc(event.summary)}</strong><br>${esc(formatTime(event.occurred_at))}</li>`).join('')}</ul>`
        : '<p>尚未查询</p>';
      if (['create_listing','clarify'].includes(intent)) populateRequirements(response.understood_requirements || {});
      else document.getElementById('requirementsEditor').classList.remove('visible');
    }

    function renderPriceConfirmation(prompt) {
      const container = document.getElementById('priceConfirmation');
      if (!prompt) {
        container.classList.remove('visible');
        document.getElementById('priceEvidence').value = '';
        return;
      }
      container.classList.add('visible');
      const direction = prompt.position === 'above_market' ? '高于' : prompt.position === 'below_market' ? '低于' : '偏离';
      document.getElementById('priceConfirmationSummary').textContent = `目标售价${direction}核心可比市场。系统已暂停商品文案和促销生成，确认前不会修改店铺。证据可信度：${qualityLabel(prompt.evidence_quality)}；核心样本 ${prompt.core_sample_count} 个，排除脏数据 ${prompt.excluded_sample_count} 个。`;
      document.getElementById('priceTarget').textContent = money(prompt.target_price);
      document.getElementById('priceReference').textContent = money(prompt.core_reference_price);
      document.getElementById('priceAcceptance').textContent = moneyBand(prompt.acceptance_band);
      document.getElementById('priceDeviation').textContent = prompt.deviation_rate == null ? '-' : `${direction} ${Math.abs(Number(prompt.deviation_rate) * 100).toFixed(1)}%`;
      const adopt = prompt.options.find(item => item.action === 'adopt_suggested_price') || {};
      document.getElementById('adoptPriceDescription').textContent = `建议区间 ${moneyBand(prompt.suggested_price_range)}${adopt.suggested_price ? `，本次将采用 ${money(adopt.suggested_price)}` : ''}。`;
      document.getElementById('priceOptionError').style.display = 'none';
    }

    function qualityLabel(value) { return ({high:'高',medium:'中',low:'低',unavailable:'不可用'})[value] || '待评估'; }
    function evidenceQualityFromMarket(layers) { return layers.mode === 'decision_ready' ? (layers.distribution_status === 'stable' ? 'high' : 'medium') : 'low'; }

    function populateRequirements(req) {
      document.getElementById('requirementsEditor').classList.add('visible');
      document.getElementById('reqCategory').value = req.category || '';
      document.getElementById('reqAudience').value = req.target_audience || '';
      document.getElementById('reqCost').value = req.cost ?? '';
      document.getElementById('reqPrice').value = req.target_price ?? '';
      document.getElementById('reqInventory').value = req.inventory ?? '';
      document.getElementById('reqMargin').value = req.min_margin_rate == null ? '' : Number(req.min_margin_rate) * 100;
      document.getElementById('reqFeatures').value = (req.confirmed_features || []).join('、');
    }

    function correctedMessage() {
      const category = document.getElementById('reqCategory').value.trim();
      const audience = document.getElementById('reqAudience').value.trim();
      const cost = document.getElementById('reqCost').value;
      const price = document.getElementById('reqPrice').value;
      const inventory = document.getElementById('reqInventory').value;
      const margin = document.getElementById('reqMargin').value;
      const features = document.getElementById('reqFeatures').value.trim();
      return `我要上架一款成本 ${cost} 元的${category}，目标售价 ${price} 元，主要面向${audience}，库存 ${inventory} 件，毛利率不能低于 ${margin}%。已确认的产品功能：${features || '暂无补充'}。已确认的产品形态：未确认。运营目标：根据修正后的明确需求生成安全可执行的上新方案。`;
    }

    function renderChecks(checks) { return `<ul>${Object.entries(checks).map(([name,ok]) => `<li>${ok ? '通过' : '未通过'}：${esc(checkLabel(name))}</li>`).join('')}</ul>`; }
    function checkLabel(name) { return ({product_exists:'商品已创建',title_match:'标题一致',price_match:'售价一致',stock_match:'库存一致',bullets_match:'卖点一致',coupon_match:'优惠一致',promotion_exists:'促销已创建',promotion_coupon_match:'促销金额一致'})[name] || name; }
    function evidenceLabel(name) { return ({forecast_demand:'查询需求预测',query_campaign_history:'查询历史活动',analyze_competitor_price_trends:'分析竞品价格变化'})[name] || name; }
    function analyticsToolLabel(name) { return ({get_sales_metrics:'读取期间销量、销售额与转化',compare_sales_periods:'对比上一等长周期',get_campaign_performance:'读取活动表现',get_inventory_history:'读取库存流水'})[name] || name; }
    function sourceLabel(name) { return ({synthetic_demo:'模拟演示数据',imported_file:'导入文件',platform_api:'电商平台接口'})[name] || name || '未知来源'; }
    function outcomeTitle(outcome) { return ({awaiting_approval:'方案已准备好，等待你的确认',completed:'已同步并完成核对',business_rejected:'当前条件需要调整',technical_failed:'系统执行遇到技术问题',waiting_for_input:'还需要你补充信息',advisory:'已切换为建议模式',read_only_completed:'只读分析已完成',answered:'已回答',out_of_scope:'当前请求不执行',running:'正在处理'})[outcome] || '任务处理中'; }
    function outcomeLabel(outcome) { return ({awaiting_approval:'待确认',completed:'已完成',business_rejected:'需调整',technical_failed:'技术失败',waiting_for_input:'待补充',advisory:'仅建议',read_only_completed:'只读完成',answered:'已回答',out_of_scope:'未执行'})[outcome] || outcome; }
    function intentLabel(intent) { return ({create_listing:'商品上新',modify_listing:'修改商品',market_research:'市场调研',product_detail:'商品详情',product_performance:'销售表现',task_status:'任务状态',clarify:'信息补充',general_chat:'普通问答',out_of_scope:'超出范围'})[intent] || intent; }
    function scopeLabel(scope) { return ({market_catalog:'市场商品样本',competitor_samples:'竞品样本',review_aggregates:'评论汇总',listing_draft:'商品草稿',pricing_plan:'定价方案',inventory:'库存信息',conversation_tasks:'会话任务',task_checkpoint:'任务状态',product_ledger:'商品账本',task_product_links:'任务商品关联',seller_snapshot:'店铺快照',daily_product_metrics:'每日商品指标',campaign_metrics:'活动表现',inventory_movements:'库存流水'})[scope] || scope; }
    function setBusy(busy) { document.getElementById('sendButton').disabled = busy || !linkedReady; document.getElementById('executeButton').disabled = busy || batchExecutionPending || (!currentResponse?.approval_required && selectedBatchCount() === 0); document.getElementById('retryButton').disabled = busy || batchExecutionPending || selectedRetryCount() === 0; document.getElementById('composer').classList.toggle('busy', busy); }
    function showError(message) { const bar=document.getElementById('errorBar'); bar.textContent=message; bar.style.display='block'; }
    function hideError() { document.getElementById('errorBar').style.display='none'; }
    function readError(payload,fallback) { const detail=payload?.detail; if(typeof detail==='string') return detail; if(detail?.issues) return `服务未就绪：${detail.issues.join('、')}`; return detail?.message || detail?.error || payload?.message || payload?.error || fallback; }

    async function loadConversations() {
      try {
        const params = new URLSearchParams({limit:'50',approval_status:document.getElementById('approvalFilter').value});
        const query = document.getElementById('historySearch').value.trim();
        const product = document.getElementById('productFilter').value;
        if (query) params.set('query', query);
        if (product) params.set('product_id', product);
        const response = await fetch(`/api/copilot/conversations?${params}`);
        const payload = await readJsonResponse(response);
        if (!response.ok) throw new Error(readError(payload, '会话列表加载失败'));
        renderConversationList(payload.conversations || []);
      } catch (error) {
        document.getElementById('historyList').innerHTML = `<div class="history-empty">${esc(error.message)}</div>`;
      }
    }

    async function loadProductFilter() {
      try {
        const response = await fetch('/api/copilot/products?limit=100');
        const products = await readJsonResponse(response);
        if (!response.ok || !Array.isArray(products)) return;
        const select = document.getElementById('productFilter');
        select.innerHTML = '<option value="">全部商品</option>' + products.map(item => `<option value="${esc(item.product_id)}">${esc(item.title)}</option>`).join('');
      } catch (_error) {}
    }

    function renderConversationList(conversations) {
      const container = document.getElementById('historyList');
      if (!conversations.length) {
        container.innerHTML = '<div class="history-empty">暂无历史会话。发送第一条消息后会自动保存。</div>';
        return;
      }
      container.innerHTML = conversations.map(item => `<button class="history-item ${item.conversation_id === currentConversationId ? 'active' : ''}" type="button" data-conversation="${esc(item.conversation_id)}"><strong>${esc(item.title)}</strong><span>${esc(item.last_message || '尚无消息')}</span><span class="history-time">${esc(outcomeLabel(item.last_task_status) || '新会话')} · ${esc(formatTime(item.updated_at))}</span></button>`).join('');
      container.querySelectorAll('[data-conversation]').forEach(button => button.addEventListener('click', () => openConversation(button.dataset.conversation)));
    }

    function renderHistorySelection() {
      document.querySelectorAll('[data-conversation]').forEach(button => button.classList.toggle('active', button.dataset.conversation === currentConversationId));
    }

    async function openConversation(conversationId) {
      if (!conversationId) return;
      invalidateBatchRecovery();
      activeEventSource?.close();
      activeEventSource = null;
      liveMessage?.remove();
      liveMessage = null;
      setBusy(true);
      try {
        const response = await fetch(`/api/copilot/conversations/${encodeURIComponent(conversationId)}`);
        const payload = await readJsonResponse(response);
        if (!response.ok) throw new Error(readError(payload, '历史会话加载失败'));
        currentConversationId = conversationId;
        history.replaceState({}, '', `/user?conversation_id=${encodeURIComponent(conversationId)}`);
        currentResponse = payload.latest_response;
        const messages = document.getElementById('messages');
        messages.innerHTML = '';
        const storedMessages = payload.detail.messages || [];
        let evidenceIndex = -1;
        storedMessages.forEach((message, index) => {
          if (message.role === 'assistant' && message.task_id === currentResponse?.task_id) evidenceIndex = index;
        });
        storedMessages.forEach((message, index) => {
          const evidence = index === evidenceIndex ? currentResponse : null;
          addMessage(message.role, message.content, evidence);
        });
        if (!payload.detail.messages?.length) addWelcomeMessage();
        if (currentResponse) renderResponse(currentResponse);
        else {
          const empty = document.getElementById('workspaceEmpty');
          empty.style.display = 'grid';
          empty.innerHTML = '<div><strong>等待你的任务</strong><span>方案、定价和风险检查会显示在这里</span></div>';
          document.getElementById('workspaceContent').classList.remove('visible');
        }
        renderHistorySelection();
        document.body.classList.remove('history-open');
      } catch (error) { showError(error.message); }
      finally { if (!activeEventSource) setBusy(false); }
      await resumeActiveStream(conversationId);
    }

    async function resumeActiveStream(conversationId) {
      try {
        const response = await fetch(`/api/copilot/conversations/${encodeURIComponent(conversationId)}/active-stream`);
        const active = await readJsonResponse(response);
        if (!response.ok) throw new Error(readError(active, '运行中任务状态加载失败'));
        if (!active?.events_url) return false;
        setBusy(true);
        startEventStream(`${active.events_url}?after=0`);
        return true;
      } catch (error) {
        showError(error.message);
        return false;
      }
    }

    function formatTime(value) {
      if (!value) return '';
      const date = new Date(value);
      return Number.isNaN(date.valueOf()) ? '' : date.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
    }

    async function loadStatus() {
      try {
        const response = await fetch('/linked/status');
        const status = await readJsonResponse(response);
        linkedReady = Boolean(status.ready);
        const llmService = document.getElementById('llmService');
        const browserService = document.getElementById('browserService');
        llmService.textContent = status.llm?.real_llm_enabled
          ? `${status.llm.ready ? '可用' : '已配置但未就绪'}：真实模型 ${status.llm.model}`
          : `真实模型未连接 · 当前测试桩：${status.llm?.provider || 'deterministic'}`;
        browserService.textContent = status.browser?.real_browser_enabled
          ? `${status.browser.ready ? '可用' : '已配置但未就绪'}：Playwright`
          : `真实浏览器未连接 · 当前测试桩：${status.browser?.backend || 'mock browser'}`;
        llmService.className = status.llm?.ready ? 'ready' : 'degraded';
        browserService.className = status.browser?.ready ? 'ready' : 'degraded';
      } catch (_error) { linkedReady=false; document.getElementById('llmService').textContent='模型服务不可用'; document.getElementById('browserService').textContent='店铺服务不可用'; document.getElementById('llmService').className='failed'; document.getElementById('browserService').className='failed'; }
      document.getElementById('sendButton').disabled = !linkedReady;
    }

    document.getElementById('sendButton').addEventListener('click', () => sendMessage(document.getElementById('messageInput').value));
    document.getElementById('messageInput').addEventListener('keydown', event => { if(event.key==='Enter' && !event.shiftKey) { event.preventDefault(); sendMessage(event.target.value); } });
    bindExampleButtons();
    document.getElementById('executeButton').addEventListener('click', approveCurrent);
    document.getElementById('retryButton').addEventListener('click', retryBatchItems);
    document.getElementById('applyRequirements').addEventListener('click', () => sendMessage(correctedMessage()));
    document.getElementById('adoptPriceButton').addEventListener('click', () => confirmPrice('adopt_suggested_price'));
    document.getElementById('keepPriceButton').addEventListener('click', () => confirmPrice('keep_original_with_evidence'));
    document.getElementById('marketOnlyButton').addEventListener('click', () => confirmPrice('market_analysis_only'));
    document.getElementById('newConversation').addEventListener('click', resetConversation);
    document.getElementById('historyToggle').addEventListener('click', () => document.body.classList.toggle('history-open'));
    const sidebarToggle = document.getElementById('sidebarToggle');
    function setSidebarCollapsed(collapsed) {
      document.body.classList.toggle('history-collapsed', collapsed);
      sidebarToggle.textContent = collapsed ? '›' : '‹';
      sidebarToggle.title = collapsed ? '展开会话列表' : '收起会话列表';
      sidebarToggle.setAttribute('aria-label', sidebarToggle.title);
      sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
      localStorage.setItem('ecompilot-history-collapsed', collapsed ? '1' : '0');
    }
    sidebarToggle.addEventListener('click', () => setSidebarCollapsed(!document.body.classList.contains('history-collapsed')));
    let historyTimer = null;
    document.getElementById('historySearch').addEventListener('input', () => { clearTimeout(historyTimer); historyTimer=setTimeout(loadConversations,220); });
    document.getElementById('productFilter').addEventListener('change', loadConversations);
    document.getElementById('approvalFilter').addEventListener('change', loadConversations);
    document.querySelectorAll('[data-mobile-mode]').forEach(button => button.addEventListener('click', () => {
      const mode = button.dataset.mobileMode;
      document.body.classList.toggle('mobile-view-chat', mode === 'chat');
      document.body.classList.toggle('mobile-view-results', mode === 'results');
      document.querySelectorAll('[data-mobile-mode]').forEach(item => item.classList.toggle('active', item === button));
    }));
    document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item===button)); document.querySelectorAll('.tab-panel').forEach(item => item.classList.toggle('active', item.id===button.dataset.panel)); }));
    async function bootstrap() {
      setSidebarCollapsed(localStorage.getItem('ecompilot-history-collapsed') === '1');
      await loadStatus();
      await loadProductFilter();
      await loadConversations();
      const conversationId = new URLSearchParams(location.search).get('conversation_id');
      if (conversationId) await openConversation(conversationId);
    }
    bootstrap();
  </script>
</body>
</html>
"""
