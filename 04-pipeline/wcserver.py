# -*- coding: utf-8 -*-
"""
wcserver.py — 04-pipeline 的抽象网页服务

一个本地网页（零依赖，标准库 + 内联前端），让用户：
  1. 选一个**行业模板**（家教/买卖/活动/群聊情报/求职），或**自己定义字段**新建模板
  2. 指定要扫的会话（按名字模糊匹配，或全选）
  3. 点一下，后端把整条流水线跑完：切块 → 抽取 → 按实体合并 → 落库
  4. 结果以**表格**或**管理面板**两种形式呈现，可导出 CSV

设计约束：
  · 视觉沿用 00-core/wc-design.css（深色 + 微信绿/电光蓝），遵守两条性能红线
  · 长任务（抽取要几十秒到几分钟）走**后台线程 + 轮询进度**，前端不卡
  · 结果表用分页渲染，避免一次塞几千行 DOM（性能）
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "00-core")
for p in (CORE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from wcstore import Store  # noqa: E402, F401
import wcpipeline as wcp  # noqa: E402

STATE = {"running": False, "tpl": "", "done": 0, "total": 0,
         "msg": "", "result": None, "error": "", "started": 0}
LOCK = threading.Lock()


def _load_css() -> str:
    p = os.path.join(CORE, "wc-design.css")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>业务线流水线</title>
<style>
__CSS__
.pipe-head{display:flex;align-items:flex-end;justify-content:space-between;
  gap:16px;flex-wrap:wrap;margin-bottom:4px}
.pipe-title{font-size:19px;font-weight:650}
.pipe-sub{font-size:12px;color:var(--txt-3);margin-top:5px}
.tpl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(258px,1fr));gap:13px}
.tpl-card{background:linear-gradient(180deg,var(--card),var(--card-2));
  border:1px solid var(--line);border-radius:var(--r);padding:15px;cursor:pointer;
  transition:border-color .15s,transform .15s;display:flex;flex-direction:column;gap:9px}
.tpl-card:hover{border-color:var(--line-2);transform:translateY(-2px)}
.tpl-card.on{border-color:rgba(7,193,96,.55);box-shadow:0 0 0 1px rgba(7,193,96,.2)}
.tpl-top{display:flex;align-items:center;gap:10px}
.tpl-ico{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;
  font-size:17px;background:rgba(255,255,255,.07)}
.tpl-nm{font-size:14px;font-weight:620}
.tpl-desc{font-size:11.5px;color:var(--txt-3);line-height:1.5;min-height:34px}
.tpl-fields{display:flex;flex-wrap:wrap;gap:4px}
.tpl-fields .tag{font-size:10px;padding:1px 6px}
.runbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.runbar input[type=text],.runbar input[type=number]{
  background:#0c1018;border:1px solid var(--line);border-radius:8px;padding:7px 11px;
  font-size:12.5px;outline:none}
.runbar input:focus{border-color:var(--blue)}
.prog{height:6px;border-radius:6px;background:rgba(255,255,255,.07);overflow:hidden;margin-top:10px}
.prog div{height:100%;width:0;border-radius:6px;background:linear-gradient(90deg,var(--green),var(--cyan));
  transition:width .3s}
.tbl-wrap{overflow:auto;max-height:640px;border-radius:var(--r-s);border:1px solid var(--line)}
/* 列头不能换行：否则「状态」这种两字表头会被挤成竖排（踩过） */
.tbl th{white-space:nowrap}
.tbl td{min-width:64px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.rec{background:rgba(255,255,255,.032);border:1px solid var(--line);border-radius:var(--r-s);padding:13px}
.rec:hover{border-color:var(--line-2)}
.rec-h{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:9px}
.rec-e{font-size:14px;font-weight:620}
.kv{display:grid;grid-template-columns:76px 1fr;gap:3px 9px;font-size:12px}
.kv .k{color:var(--txt-3)}
.kv .v{color:var(--txt);word-break:break-all}
.miss{color:var(--rose);font-size:11px}
.seg{display:flex;gap:4px;background:rgba(255,255,255,.05);padding:3px;border-radius:9px}
.seg button{padding:5px 12px;border-radius:7px;font-size:12px;color:var(--txt-2)}
.seg button.on{background:var(--card-2);color:#fff}
.editor{display:flex;flex-direction:column;gap:8px}
.frow{display:grid;grid-template-columns:120px 90px 1fr 60px;gap:8px;align-items:center}
.frow input,.frow select{background:#0c1018;border:1px solid var(--line);
  border-radius:7px;padding:6px 9px;font-size:12px;outline:none;width:100%}
</style></head>
<body>
<div class="bg-glow" aria-hidden="true"></div>
<div class="top">
  <div class="brand"><div class="logo">🏭</div>
    <div><h1>业务线流水线</h1>
      <div class="sub">选一个行业模板 → 指定会话 → 自动抽成管理表</div></div></div>
  <div class="spacer"></div>
  <button class="btn ghost small" id="btnNew">+ 自定义模板</button>
</div>
<div class="page">

  <div class="section">
    <div class="card">
      <div class="card-head"><span>1 · 选择业务线</span>
        <span class="hint" id="tplHint">点卡片切换模板</span></div>
      <div class="tpl-grid" id="tplGrid"></div>
    </div>
  </div>

  <div class="section" id="editorWrap" style="display:none">
    <div class="card">
      <div class="card-head"><span>自定义模板</span>
        <span class="hint">定义你自己的字段，比如「客户 / 合同额 / 阶段」</span></div>
      <div class="editor">
        <div class="runbar">
          <input type="text" id="ntName" placeholder="模板名，如「我的客户跟进」" style="width:220px">
          <input type="text" id="ntEnt" placeholder="实体叫什么，如「客户」" style="width:160px">
          <input type="text" id="ntDesc" placeholder="一句话说明（可选）" style="width:280px">
        </div>
        <div id="fields"></div>
        <div class="runbar">
          <button class="btn ghost small" id="btnAddField">+ 加字段</button>
          <button class="btn small green" id="btnSaveTpl">保存模板</button>
          <button class="btn ghost small" id="btnCancelTpl">取消</button>
        </div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="card">
      <div class="card-head"><span>2 · 指定范围</span>
        <span class="hint">会话名模糊匹配，留空 = 扫消息量最多的几个</span></div>
      <div class="runbar">
        <input type="text" id="pattern" placeholder="会话名包含…（如：家教）" style="width:200px">
        <input type="number" id="days" value="180" min="1" max="3650" style="width:88px" title="最近多少天">
        <span class="tiny dim">天</span>
        <input type="number" id="top" value="5" min="1" max="40" style="width:70px" title="最多扫几个会话">
        <span class="tiny dim">个会话</span>
        <input type="number" id="chunks" value="30" min="1" max="300" style="width:70px" title="每会话最多几块">
        <span class="tiny dim">块/会话</span>
        <button class="btn green" id="btnRun">开始抽取</button>
        <span class="tiny dim" id="runHint"></span>
      </div>
      <div class="prog" id="progWrap" style="display:none"><div id="prog"></div></div>
    </div>
  </div>

  <div class="section">
    <div class="card">
      <div class="card-head">
        <span>3 · 结果</span>
        <span style="display:flex;gap:8px;align-items:center">
          <span class="seg" id="viewSeg">
            <button data-v="table" class="on">表格</button>
            <button data-v="cards">面板</button>
          </span>
          <button class="btn ghost small" id="btnCsv">导出 CSV</button>
          <button class="btn ghost small" id="btnRefresh">刷新</button>
        </span>
      </div>
      <div id="results"><div class="empty"><span class="big-ico">🏭</span>
        还没有结果 —— 选好模板和范围，点「开始抽取」</div></div>
    </div>
  </div>

  <div class="report-foot">
    04-pipeline · 抽取引擎是配置驱动的，新增行业只需定义字段<br>
    结果含聊天原文片段，分享前请自行确认
  </div>
</div>
<script>
const S = {templates:[], tpl:'', view:'table', data:[]};
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const esc = s => String(s==null?'':s).replace(/[&<>"']/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(p, opt){ const r = await fetch(p, opt); return r.json(); }
const post = (p,b)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(b||{})});

function renderTemplates(){
  $('#tplGrid').innerHTML = S.templates.map(t=>`
    <div class="tpl-card ${t.id===S.tpl?'on':''}" data-id="${t.id}">
      <div class="tpl-top"><div class="tpl-ico">${t.icon||'⚙️'}</div>
        <div><div class="tpl-nm">${esc(t.name)}</div>
          <div class="tiny dim">${esc(t.hint||'')}</div></div></div>
      <div class="tpl-desc">${esc(t.desc||'')}</div>
      <div class="tpl-fields">${(t.fields||[]).map(f=>
        `<span class="tag">${esc(f.label)}</span>`).join('')}</div>
    </div>`).join('');
  $$('#tplGrid .tpl-card').forEach(c=>c.onclick=()=>{
    S.tpl=c.dataset.id; renderTemplates(); loadResults();
  });
  const t = S.templates.find(x=>x.id===S.tpl);
  $('#tplHint').textContent = t? `当前：${t.name} · 实体=「${t.entity_label||'条目'}」` : '';
}

function renderResults(){
  const t = S.templates.find(x=>x.id===S.tpl) || {fields:[],entity_label:'条目'};
  const d = S.data;
  if(!d.length){ $('#results').innerHTML =
    '<div class="empty"><span class="big-ico">📭</span>这个模板还没有抽取结果</div>'; return; }
  if(S.view==='cards'){
    $('#results').innerHTML = '<div class="cards">' + d.slice(0,120).map(g=>`
      <div class="rec">
        <div class="rec-h"><span class="rec-e">${esc(g.entity)}</span>
          <span class="tag">${g.n_sources} 条来源</span></div>
        <div class="kv">${t.fields.map(f=>{
          const v = g.fields[f.key];
          if(!v) return '';
          return `<span class="k">${esc(f.label)}</span><span class="v">${esc(v)}</span>`;
        }).join('')}
        ${g.missing&&g.missing.length?`<span class="k">缺</span><span class="miss">${esc(g.missing.join('、'))}</span>`:''}
        ${g.quotes&&g.quotes[0]?`<span class="k">依据</span><span class="v dim">${esc(g.quotes[0]).slice(0,60)}</span>`:''}
        </div>
      </div>`).join('') + '</div>'
      + (d.length>120?`<div class="tiny dim" style="margin-top:10px">共 ${d.length} 条，面板只显示前 120 条，完整数据用「导出 CSV」</div>`:'');
    return;
  }
  $('#results').innerHTML = `<div class="tbl-wrap"><table class="tbl">
    <thead><tr><th>${esc(t.entity_label||'条目')}</th>
      ${t.fields.map(f=>`<th>${esc(f.label)}</th>`).join('')}
      <th class="num">来源</th><th>最近</th></tr></thead><tbody>
    ${d.slice(0,300).map(g=>`<tr>
      <td><b>${esc(g.entity)}</b></td>
      ${t.fields.map(f=>`<td>${esc(g.fields[f.key]||'—')}</td>`).join('')}
      <td class="num">${g.n_sources}</td><td class="dim">${esc(g.last_day||'')}</td>
    </tr>`).join('')}
    </tbody></table></div>`
    + (d.length>300?`<div class="tiny dim" style="margin-top:8px">共 ${d.length} 条，表格只显示前 300 条</div>`:'');
}

async function loadResults(){
  if(!S.tpl) return;
  const r = await api('/api/results?tpl='+encodeURIComponent(S.tpl));
  S.data = r.rows||[]; renderResults();
  const st = r.stats||{};
  $('#runHint').textContent = st.records?
    `已有 ${st.records} 条原始记录 / ${st.entities} 个实体` : '';
}

async function poll(){
  const st = await api('/api/state');
  if(st.running){
    $('#progWrap').style.display='block';
    $('#prog').style.width = Math.max(6, st.total? (st.done/st.total*100):12) + '%';
    $('#runHint').textContent = st.msg || '抽取中…';
    setTimeout(poll, 1200); return;
  }
  $('#progWrap').style.display='none';
  if(st.error){ $('#runHint').textContent = '失败：'+st.error; }
  else if(st.result){ 
    const r = st.result;
    $('#runHint').textContent = `完成：${r.chunks} 块 / 新增 ${r.new} 条 · ${r.seconds}s · ¥${r.cost.toFixed(4)}`;
    await loadResults();
  }
}

async function run(){
  if(!S.tpl) return alert('先选一个模板');
  const r = await post('/api/run', {
    tpl:S.tpl, pattern:$('#pattern').value.trim(),
    days:+$('#days').value, top:+$('#top').value, chunks:+$('#chunks').value});
  if(r.error) return alert(r.error);
  $('#runHint').textContent='已启动…'; poll();
}

// ---- 自定义模板编辑器 ----
function addFieldRow(f){
  f = f||{label:'',key:'',type:'text',desc:''};
  const div = document.createElement('div');
  div.className='frow';
  div.innerHTML = `<input placeholder="显示名" value="${esc(f.label)}">
    <select><option value="text">文本</option><option value="enum">枚举</option>
      <option value="number">数字</option></select>
    <input placeholder="抽取说明（给模型看，越具体越准）" value="${esc(f.desc)}">
    <button class="btn ghost small">删</button>`;
  div.querySelector('select').value = f.type||'text';
  div.querySelector('button').onclick=()=>div.remove();
  $('#fields').appendChild(div);
}
$('#btnNew').onclick = ()=>{ $('#editorWrap').style.display='block';
  $('#fields').innerHTML=''; ['名称','金额','阶段','备注'].forEach(l=>addFieldRow({label:l})); };
$('#btnCancelTpl').onclick = ()=>$('#editorWrap').style.display='none';
$('#btnAddField').onclick = ()=>addFieldRow();
$('#btnSaveTpl').onclick = async ()=>{
  const name=$('#ntName').value.trim(); if(!name) return alert('填个模板名');
  const fields=[];
  $$('#fields .frow').forEach(r=>{
    const ins=r.querySelectorAll('input'), sel=r.querySelector('select');
    const label=ins[0].value.trim(); if(!label) return;
    fields.push({key:'f'+fields.length, label, type:sel.value, desc:ins[1].value.trim()});
  });
  if(!fields.length) return alert('至少一个字段');
  const r = await post('/api/templates', {tpl:{name, entity_label:$('#ntEnt').value.trim()||'条目',
    desc:$('#ntDesc').value.trim(), icon:'⚙️', fields}});
  if(r.error) return alert(r.error);
  S.templates = r.templates; S.tpl = r.tpl.id;
  $('#editorWrap').style.display='none';
  renderTemplates(); loadResults();
};
$('#btnRun').onclick = run;
$('#btnRefresh').onclick = loadResults;
$('#btnCsv').onclick = ()=>{ if(S.tpl) location.href='/api/csv?tpl='+encodeURIComponent(S.tpl); };
$('#viewSeg').onclick = e=>{ const b=e.target.closest('button'); if(!b) return;
  S.view=b.dataset.v; $$('#viewSeg button').forEach(x=>x.classList.toggle('on',x===b));
  renderResults(); };

(async ()=>{
  const r = await api('/api/templates');
  S.templates = r.templates||[];
  S.tpl = S.templates.length ? S.templates[0].id : '';
  renderTemplates(); loadResults();
})();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, body: bytes, ctype="application/json; charset=utf-8", code=200,
              extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), code=code)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p in ("/", "/index.html"):
            css = _load_css()
            return self._send(PAGE.replace("__CSS__", css).encode("utf-8"),
                              "text/html; charset=utf-8")
        if p == "/api/templates":
            return self._json({"templates": wcp.load_templates()})
        if p == "/api/state":
            with LOCK:
                return self._json(dict(STATE))
        if p == "/api/results":
            tpl = q.get("tpl", [""])[0]
            pipe = wcp.Pipeline(verbose=False)
            return self._json({"rows": pipe.merge(tpl),
                               "stats": pipe.stats(tpl)})
        if p == "/api/csv":
            tpl = q.get("tpl", [""])[0]
            pipe = wcp.Pipeline(verbose=False)
            path = pipe.export_csv(tpl)
            with open(path, "rb") as f:
                data = f.read()
            return self._send(data, "text/csv; charset=utf-8", extra={
                "Content-Disposition":
                    f'attachment; filename="{os.path.basename(path)}"'})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        b = self._body()
        if p == "/api/templates":
            t = wcp.save_template(b.get("tpl") or {})
            return self._json({"ok": True, "tpl": t,
                               "templates": wcp.load_templates()})
        if p == "/api/run":
            with LOCK:
                if STATE["running"]:
                    return self._json({"error": "已有一个任务在跑，等它结束"})
                STATE.update(running=True, tpl=b.get("tpl", ""), done=0, total=0,
                             msg="启动中…", result=None, error="",
                             started=time.time())
            threading.Thread(target=_worker, args=(b,), daemon=True).start()
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)


def _worker(b: dict):
    """后台跑流水线。用回调更新进度，前端轮询 /api/state。"""
    try:
        pipe = wcp.Pipeline(verbose=False)

        orig = pipe._log

        def log(m):
            with LOCK:
                STATE["msg"] = str(m)[-160:]

        pipe._log = log
        with LOCK:
            STATE["msg"] = "扫描会话…"
        r = pipe.run(b.get("tpl", ""), pattern=b.get("pattern", ""),
                     days=int(b.get("days") or 180), top=int(b.get("top") or 5),
                     max_chunks=int(b.get("chunks") or 30))
        with LOCK:
            STATE.update(running=False, result=r, msg="完成",
                         done=r["chunks"], total=r["chunks"])
    except Exception as e:
        with LOCK:
            STATE.update(running=False, error=f"{type(e).__name__}: {e}")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print("=" * 60)
    print("  业务线流水线")
    print(f"  {url}")
    print("=" * 60)
    if a.open:
        threading.Thread(target=lambda: (time.sleep(1), webbrowser.open(url)),
                         daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[bye]")


if __name__ == "__main__":
    main()
