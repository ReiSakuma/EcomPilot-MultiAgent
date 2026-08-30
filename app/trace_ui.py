from __future__ import annotations


TRACE_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EcomPilot Run Traces</title>
  <style>
    :root { --ink:#172033; --muted:#667085; --line:#d8dee8; --panel:#fff; --soft:#f5f7fb; --accent:#0f766e; --bad:#b42318; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--ink); background:#eef2f7; }
    header { min-height:64px; padding:14px 20px; display:flex; align-items:center; justify-content:space-between; gap:12px; background:var(--panel); border-bottom:1px solid var(--line); }
    h1 { margin:0; font-size:20px; letter-spacing:0; }
    button { min-height:36px; border:1px solid var(--line); border-radius:6px; background:#fff; padding:0 12px; cursor:pointer; }
    main { display:grid; grid-template-columns:minmax(280px,360px) minmax(0,1fr); min-height:calc(100vh - 65px); }
    aside { border-right:1px solid var(--line); background:var(--panel); min-width:0; }
    .aside-head,.detail-head { padding:14px 16px; border-bottom:1px solid var(--line); }
    .runs { max-height:calc(100vh - 122px); overflow:auto; }
    .run { width:100%; min-height:76px; padding:11px 14px; text-align:left; border:0; border-bottom:1px solid var(--line); border-radius:0; }
    .run.active { background:#ecfdf5; box-shadow:inset 3px 0 0 var(--accent); }
    .run strong,.run small { display:block; overflow-wrap:anywhere; }
    .run small { margin-top:5px; color:var(--muted); }
    .detail { min-width:0; }
    .summary { display:grid; grid-template-columns:repeat(6,minmax(100px,1fr)); background:var(--panel); border-bottom:1px solid var(--line); }
    .stat { padding:12px; border-right:1px solid var(--line); min-width:0; }
    .stat:last-child { border-right:0; }
    .stat small,.event small { display:block; color:var(--muted); margin-bottom:5px; }
    .stat strong { display:block; overflow-wrap:anywhere; }
    .timeline { padding:14px; display:grid; gap:8px; }
    .event { display:grid; grid-template-columns:52px minmax(130px,180px) minmax(120px,180px) minmax(0,1fr); gap:10px; align-items:start; padding:10px 12px; background:var(--panel); border:1px solid var(--line); border-radius:6px; }
    .event code { overflow-wrap:anywhere; }
    .failed { color:var(--bad); }
    pre { margin:7px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; color:var(--muted); font-size:12px; max-height:220px; overflow:auto; }
    @media(max-width:900px) { main { grid-template-columns:1fr; } aside { border-right:0; border-bottom:1px solid var(--line); } .runs { max-height:260px; } .summary { grid-template-columns:repeat(2,minmax(0,1fr)); } .event { grid-template-columns:44px minmax(100px,1fr); } .event pre { grid-column:1/-1; } }
  </style>
</head>
<body>
  <header><h1>EcomPilot Run Traces</h1><div><button onclick="location.href='/'">用户工作台</button> <button onclick="location.href='/ops'">运维控制台</button> <button onclick="loadRuns()">刷新</button></div></header>
  <main>
    <aside><div class="aside-head"><strong>Runs</strong></div><div class="runs" id="runs"></div></aside>
    <section class="detail">
      <div class="detail-head"><strong id="title">选择一次运行</strong></div>
      <div class="summary" id="summary"></div>
      <div class="timeline" id="timeline"></div>
    </section>
  </main>
  <script>
    const pageParams = new URLSearchParams(location.search);
    const requestedRunId = pageParams.get('run_id');
    let selected = requestedRunId || '';
    let followLatest = pageParams.get('pin') !== '1';
    let refreshing = false;
    const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    async function loadRuns() {
      const response = await fetch('/api/traces?limit=50');
      const runs = await response.json();
      if (followLatest && runs.length) selected = runs[0].run_id;
      document.getElementById('runs').innerHTML = runs.map(run => `<button class="run ${selected===run.run_id?'active':''}" onclick="selectRun('${esc(run.run_id)}')"><strong>${esc(run.run_id)}</strong><small>${esc(run.status)} | ${run.event_count} events | ${run.duration_ms ?? '-'} ms</small></button>`).join('');
      if (!selected && runs.length) loadRun(runs[0].run_id);
    }
    function selectRun(runId) {
      followLatest = false;
      loadRun(runId);
    }
    async function loadRun(runId) {
      selected = runId;
      const response = await fetch('/api/traces/' + encodeURIComponent(runId));
      if (!response.ok) return;
      const data = await response.json();
      const s = data.summary;
      const pinQuery = followLatest ? '' : '&pin=1';
      history.replaceState(null, '', `/traces?run_id=${encodeURIComponent(runId)}${pinQuery}`);
      document.getElementById('title').textContent = `${s.run_id} / ${s.task_id}${s.parent_run_id ? ' / parent ' + s.parent_run_id : ''}`;
      document.getElementById('summary').innerHTML = [['状态',s.status],['事件',s.event_count],['Agent',s.agent_event_count],['工具',s.tool_call_count],['模型',s.model_call_count],['技术错误',s.error_count]].map(x => `<div class="stat"><small>${x[0]}</small><strong>${esc(x[1])}</strong></div>`).join('');
      document.getElementById('timeline').innerHTML = data.events.map(event => `<article class="event ${event.status==='failed'?'failed':''}"><code>#${esc(event.sequence ?? '-')}</code><div><small>${esc(event.event_type ?? event.step)}</small><strong>${esc(event.component_name ?? event.agent_name)}</strong></div><div><small>status / duration</small>${esc(event.status ?? '-')} / ${esc(event.duration_ms ?? '-')} ms</div><pre>${esc(JSON.stringify(event.error || event.details || {}, null, 2))}</pre></article>`).join('');
    }
    async function refreshLinkedRun() {
      if (refreshing) return;
      refreshing = true;
      try {
        await loadRuns();
        if (selected) await loadRun(selected);
      } finally {
        refreshing = false;
      }
    }
    refreshLinkedRun();
    setInterval(refreshLinkedRun, 1500);
  </script>
</body>
</html>
"""
