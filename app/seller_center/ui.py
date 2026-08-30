from __future__ import annotations

import html
import json
from typing import Any


SELLER_CENTER_EDITOR_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EcomPilot Seller Center</title>
  <style>
    :root { --ink:#172033; --muted:#667085; --line:#d0d5dd; --soft:#f5f7fa; --accent:#0f766e; --bad:#b42318; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif; color:var(--ink); background:var(--soft); }
    header { padding:18px 24px; background:#fff; border-bottom:1px solid var(--line); }
    h1 { margin:0; font-size:20px; letter-spacing:0; }
    main { max-width:1100px; margin:0 auto; padding:20px; display:grid; grid-template-columns:minmax(0,2fr) minmax(280px,1fr); gap:16px; }
    section { background:#fff; border:1px solid var(--line); border-radius:8px; padding:18px; min-width:0; }
    h2 { margin:0 0 16px; font-size:15px; letter-spacing:0; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .wide { grid-column:1/-1; }
    label { display:block; color:var(--muted); font-size:12px; margin-bottom:5px; }
    input, select, textarea { width:100%; border:1px solid var(--line); border-radius:6px; padding:9px; font:inherit; background:#fff; }
    textarea { min-height:130px; resize:vertical; }
    button { margin-top:14px; min-height:38px; padding:0 14px; border:0; border-radius:6px; background:var(--accent); color:#fff; cursor:pointer; font:inherit; }
    pre { margin:0; max-height:560px; overflow:auto; padding:12px; background:#101828; color:#e5e7eb; border-radius:6px; font-family:ui-monospace,monospace; font-size:12px; white-space:pre-wrap; overflow-wrap:anywhere; }
    #result-status { display:none; margin-bottom:10px; font-weight:700; }
    #result-status.visible { display:block; }
    .failed { color:var(--bad); }
    @media(max-width:760px){ main,.grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header><h1>EcomPilot Seller Center</h1></header>
  <main>
    <section>
      <h2>商品执行表单</h2>
      <div class="grid">
        <div><label>操作</label><select data-testid="operation"><option>update_listing</option><option>create_coupon</option><option>publish_listing</option></select></div>
        <div><label>商品 ID</label><input data-testid="product-id" /></div>
        <div class="wide"><label>标题</label><input data-testid="title" /></div>
        <div><label>价格</label><input data-testid="price" type="number" step="0.01" /></div>
        <div><label>库存</label><input data-testid="stock" type="number" step="1" /></div>
        <div><label>优惠券</label><input data-testid="coupon" type="number" step="0.01" value="0" /></div>
        <div class="wide"><label>卖点，每行一条</label><textarea data-testid="bullets"></textarea></div>
      </div>
      <button data-testid="submit-execution" onclick="submitPlan()">提交执行</button>
    </section>
    <section>
      <h2>执行结果</h2>
      <div data-testid="result-status" id="result-status"></div>
      <pre data-testid="result-json" id="result-json">{}</pre>
    </section>
  </main>
  <script>
    const executionTicket = __ECOMPILOT_TICKET_JSON__;
    async function submitPlan() {
      const value = id => document.querySelector(`[data-testid="${id}"]`).value;
      const nullableNumber = id => value(id) === '' ? null : Number(value(id));
      const plan = {
        operation: value('operation'), product_id: value('product-id'), title: value('title') || null,
        price: nullableNumber('price'), stock: nullableNumber('stock'), coupon: Number(value('coupon') || 0),
        bullets: value('bullets').split('\\n').map(item => item.trim()).filter(Boolean)
      };
      const status = document.getElementById('result-status');
      try {
        const response = await fetch('/seller-center/ui/execute', {
          method:'POST', headers:{'content-type':'application/json'},
          body:JSON.stringify({ticket:executionTicket, plan})
        });
        const payload = await response.json();
        document.getElementById('result-json').textContent = JSON.stringify(payload, null, 2);
        status.textContent = payload.status || 'failed';
        status.className = 'visible' + (response.ok ? '' : ' failed');
      } catch (error) {
        document.getElementById('result-json').textContent = JSON.stringify({error:String(error)}, null, 2);
        status.textContent = 'failed'; status.className = 'visible failed';
      }
    }
  </script>
</body>
</html>
"""


def seller_center_editor_html(ticket: str) -> str:
    ticket_json = json.dumps(ticket, ensure_ascii=True)
    return SELLER_CENTER_EDITOR_HTML.replace("__ECOMPILOT_TICKET_JSON__", ticket_json)


def product_detail_html(product_id: str, observed: dict[str, Any]) -> str:
    safe_id = html.escape(product_id)
    safe_json = html.escape(json.dumps(observed, ensure_ascii=False, indent=2))
    return f"""
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>商品 {safe_id}</title><style>
body{{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif;margin:0;color:#172033;background:#f5f7fa}}
main{{max-width:960px;margin:0 auto;padding:24px}}h1{{font-size:20px;letter-spacing:0}}
pre{{background:#101828;color:#e5e7eb;padding:16px;border-radius:8px;overflow:auto;white-space:pre-wrap;font-family:ui-monospace,monospace}}
</style></head><body><main><h1>商品 {safe_id}</h1>
<pre data-testid="observed-state">{safe_json}</pre></main></body></html>
"""
