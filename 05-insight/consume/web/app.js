/* =========================================================================
   聊天即消费 · 前端
   零依赖，原生 DOM + fetch + SSE
   ========================================================================= */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  view: 'overview',
  overview: null,
  convs: [],
  hours: [],
  breakdown: null,
  billing: null,
  models: [],
  active: null,       // 当前打开的会话 wxid
  history: null,
  filter: '',
  days: 14,
  live: false,
};

/* ---------------------------- 工具 ---------------------------- */

const nfmt = (n, d = 0) => {
  n = Number(n || 0);
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(2) + '万';
  return n.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
};
const money = (n, d = 4) => '¥' + Number(n || 0).toFixed(d);
const money2 = (n) => '¥' + Number(n || 0).toFixed(2);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function ago(t) {
  if (!t) return '—';
  const s = Math.floor(Date.now() / 1000 - t);
  if (s < 60) return '刚刚';
  if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
  if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
  return Math.floor(s / 86400) + ' 天前';
}
function hhmm(t) {
  const d = new Date(t * 1000);
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}
function dayLabel(t) {
  const d = new Date(t * 1000), now = new Date();
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, now)) return '今天';
  const y = new Date(now.getTime() - 86400000);
  if (same(d, y)) return '昨天';
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}
function hue(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 360;
  return h;
}
function avatar(name) {
  const h = hue(name || '?');
  return { bg: `linear-gradient(135deg,hsl(${h} 62% 52%),hsl(${(h + 42) % 360} 62% 42%))`,
           txt: (name || '?').trim().slice(0, 1) || '?' };
}

/* 群聊里一条消息的发送者，显示在气泡上方（后端已把 wxid 换成了昵称） */
function senderLabel(m) {
  if (m.dir === 'out' || !m.sender) return '';
  return `<div class="msg-who">${escapeHtml(m.sender)}</div>`;
}
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function toast(msg, kind = 'ok', ms = 2600) {
  const el = document.createElement('div');
  el.className = 'toast ' + (kind === 'ok' ? '' : kind);
  el.textContent = msg;
  $('#toastWrap').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = '.3s'; }, ms - 300);
  setTimeout(() => el.remove(), ms);
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
  if (!r.ok) throw new Error(path + ' -> ' + r.status);
  return r.json();
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

/* ---------------------------- 视图切换 ---------------------------- */

const CRUMB = { overview: '用量信息', chat: '聊天计费', billing: '额度与 Key', market: '模型广场', lab: 'Token 实验室' };

function setView(v) {
  state.view = v;
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  $$('.view').forEach(s => s.classList.toggle('active', s.id === 'view-' + v));
  $('#crumb').textContent = CRUMB[v] || v;
  if (v === 'chat') renderConvList();
  if (v === 'billing') renderBilling();
  if (v === 'market') renderMarket();
  if (v === 'lab') runLab();
}

/* ---------------------------- 用量信息 ---------------------------- */

function renderOverview() {
  const o = state.overview; if (!o) return;
  const b = o.billing || {};
  $('#ovRemain').textContent = nfmt(b.remaining, 2);
  $('#ovSpent').textContent = nfmt(b.total_spent, 2);
  $('#ovQuota').textContent = nfmt(b.total_quota, 2);
  $('#ovSpent2').textContent = nfmt(b.total_spent, 2);
  $('#ovSpentSub').innerHTML = `约 $${nfmt(b.cost_usd_hint, 2)} · <span id="ovLedger">${b.ledger_entries || 0}</span> 条计费记录`;
  $('#ovBar').style.width = clamp((b.used_ratio || 0) * 100, 0, 100) + '%';

  const t = o.tokens || {};
  $('#kTok').textContent = nfmt(t.total);
  $('#kIn').textContent = nfmt(t.input);
  $('#kOut').textContent = nfmt(t.output);
  $('#kThink').textContent = nfmt(t.reasoning);
  $('#kMsg').textContent = nfmt(o.messages);
  $('#kMsgSub').textContent = `${o.chats} 个会话 · ${o.last_sync || '未同步'}`;
  const ratio = t.output ? (t.input / t.output) : 0;
  $('#kRatio').textContent = ratio ? ratio.toFixed(2) + ' : 1' : '—';

  const m = o.model || {};
  $('#modelPill').textContent = m.name || '未选模型';
  $('#pricePill').textContent = `入 ¥${m.input ?? 0} / 出 ¥${m.output ?? 0} 每百万`;
  $('#sideInfo').innerHTML = o.last_error
    ? `<span style="color:var(--rose)">同步异常：${escapeHtml(o.last_error).slice(0, 60)}</span>`
    : `账本 ${b.ledger_entries || 0} 条 · ${o.db_dir ? '已连微信库' : '仅账本模式'}`;

  renderChart();
  renderRank();
  renderHours();
  renderKinds();
}

function renderChart() {
  const rows = state.timeseries || [];
  const el = $('#tsChart');
  if (!rows.length) { el.innerHTML = '<div class="empty">还没有数据</div>'; return; }
  const max = Math.max(...rows.map(r => r.input + r.output + r.reasoning), 1);
  const gridLines = 4;
  let g = '<div class="grid">';
  for (let i = 0; i <= gridLines; i++) g += `<div>${nfmt(max * (1 - i / gridLines))}</div>`;
  g += '</div>';
  const cols = rows.map(r => {
    const tot = r.input + r.output + r.reasoning;
    const h = Math.max((tot / max) * 100, tot > 0 ? 3 : 0);
    const pi = tot ? (r.input / tot) * 100 : 0, po = tot ? (r.output / tot) * 100 : 0;
    const pt = tot ? (r.reasoning / tot) * 100 : 0;
    // 注意：.stack 是 flex-direction:column，DOM 里第一个子元素渲染在最顶部。
    // 所以顺序要写成 思考→输出→输入，才能让"输入在底、思考在顶"，
    // 与 KPI 和图例一致。（写成 in/out/think 会让紫色思考段掉到底部。）
    return `<div class="col">
      <div class="tip"><b>${r.day}</b><br>
        输入 ${nfmt(r.input)} · 输出 ${nfmt(r.output)} · 思考 ${nfmt(r.reasoning)}<br>
        ${r.n} 条消息 · 约 ${money(r.cost)}</div>
      <div class="stack" style="height:${h}%">
        <div class="seg-in" style="height:${pi}%"></div>
        <div class="seg-out" style="height:${po}%"></div>
        <div class="seg-think" style="height:${pt}%"></div>
      </div>
      <div class="lbl">${r.day}</div></div>`;
  }).join('');
  el.innerHTML = g + cols;
}

function renderRank() {
  const list = (state.breakdown && state.breakdown.by_person) || [];
  const el = $('#rankList');
  if (!list.length) { el.innerHTML = '<div class="empty" style="height:120px">还没有计费数据，点「模拟 20 轮」看看效果</div>'; return; }
  el.innerHTML = list.slice(0, 14).map((p, i) => {
    const a = avatar(p.name);
    return `<div class="rank-item" data-wxid="${escapeHtml(p.wxid)}">
      <div class="rank-no">${i + 1}</div>
      <div class="rank-av" style="background:${a.bg}">${escapeHtml(a.txt)}</div>
      <div class="rank-mid">
        <div class="rank-name">${escapeHtml(p.name)}</div>
        <div class="rank-sub">${p.n} 条 · 入 ${nfmt(p.input)} / 出 ${nfmt(p.output)} · 思考 ${nfmt(p.reasoning)}</div>
      </div>
      <div class="rank-cost">${money(p.cost, 3)}</div>
    </div>`;
  }).join('');
  $$('.rank-item', el).forEach(it => it.onclick = () => {
    setView('chat'); openChat(it.dataset.wxid);
  });
}

function renderHours() {
  const rows = state.hours || [];
  const el = $('#hoursChart');
  if (!rows.length) { el.innerHTML = '<div class="empty">还没有数据</div>'; return; }
  const max = Math.max(...rows.map(r => r.input + r.output + r.reasoning), 1);
  el.innerHTML = rows.map(r => {
    const tot = r.input + r.output + r.reasoning;
    const h = clamp((tot / max) * 100, tot > 0 ? 4 : 1.5, 100);
    return `<div class="hb" style="height:${h}%">
      <div class="tip">${r.label}<br>${nfmt(tot)} tokens · ${r.n} 条</div></div>`;
  }).join('');
}

function renderKinds() {
  const rows = (state.breakdown && state.breakdown.by_kind) || [];
  const el = $('#kindList');
  const NAME = { text: '文本', sticker: '表情', image: '图片', voice: '语音', video: '视频',
                 quote: '引用', app: '链接', card: '名片', location: '位置', system: '系统' };
  if (!rows.length) { el.innerHTML = '<div class="muted tiny">还没有数据</div>'; return; }
  el.innerHTML = rows.map(r => `<div class="kind-chip">
    <b>${nfmt(r.n)}</b><span>${NAME[r.kind] || r.kind}<br>${nfmt(r.tokens)} tokens</span></div>`).join('');
}

/* ---------------------------- 聊天 ---------------------------- */

function renderConvList() {
  const el = $('#convList');
  const q = state.filter.toLowerCase();
  const rows = state.convs.filter(c => !q || (c.name || '').toLowerCase().includes(q));
  if (!rows.length) { el.innerHTML = '<div class="empty" style="height:120px">没有会话</div>'; return; }
  el.innerHTML = rows.map(c => {
    const a = avatar(c.name);
    const active = state.active === c.wxid ? ' active' : '';
    const dirMark = c.last_dir === 'out' ? '<span style="color:var(--green)">我: </span>' : '';
    return `<div class="conv${active}" data-wxid="${escapeHtml(c.wxid)}">
      <div class="conv-av" style="background:${a.bg}">${escapeHtml(a.txt)}</div>
      <div class="conv-mid">
        <div class="conv-name">${escapeHtml(c.name)}${c.is_group ? ' <span class="badge off">群</span>' : ''}</div>
        <div class="conv-last">${dirMark}${escapeHtml(c.last_text || '')}</div>
      </div>
      <div class="conv-right">
        <div class="conv-time">${c.last ? ago(c.last) : ''}</div>
        <div class="conv-cost">${money(c.cost, 3)}</div>
      </div></div>`;
  }).join('');
  $$('.conv', el).forEach(it => it.onclick = () => openChat(it.dataset.wxid));
}

async function openChat(wxid, quiet = false) {
  state.active = wxid;
  if (!quiet) renderConvList();
  const h = await api('/api/history?wxid=' + encodeURIComponent(wxid) + '&limit=300');
  if (h.msgs && h.msgs.length) h.last_key = h.msgs[h.msgs.length - 1].t;
  state.history = h;
  renderChat(quiet);
}

function renderChat(keepScroll = false) {
  const h = state.history;
  const body = $('#chatBody');
  if (!h) return;
  // 重渲染前记住滚动位置：后台刷新不应该把用户正在看的位置顶掉
  const prevTop = body.scrollTop;
  const wasAtBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
  const a = avatar(h.name);
  $('#chatTitle').textContent = h.name + (h.is_group ? '（群聊）' : '');
  $('#chatMeta').innerHTML = `${h.n} 条消息 · 这段对话累计 <b style="color:var(--amber)">${money(h.cost, 3)}</b>`;

  // 计费归属：这个人用的是哪个 Key / 哪个模型
  const pv = h.person || {};
  const avBtn = $('#chatAvBtn');
  $('#chatAv').textContent = a.txt;
  avBtn.style.background = a.bg;
  avBtn.classList.toggle('assigned', pv.source === 'person');
  avBtn.title = pv.source === 'person'
    ? `已绑定 Key：${pv.key_label}（点这里换）`
    : '还没给这个人塞 Key，点这里塞一个';
  $('#chatBilling').innerHTML =
    (pv.source === 'person'
      ? `<span class="tag">Key · ${escapeHtml(pv.key_label || '')}</span>`
      : '<span class="tag global">跟随全局</span>')
    + `<span class="tag model">${escapeHtml(pv.model_name || '')}</span>`
    + `<span>入 ¥${pv.price ? pv.price.input : 0} / 出 ¥${pv.price ? pv.price.output : 0} 每百万</span>`;

  if (!h.msgs.length) { body.innerHTML = '<div class="empty">这个会话还没有记录</div>'; $('#costStrip').innerHTML = ''; return; }

  let lastDay = '', html = '';
  for (const m of h.msgs) {
    const d = dayLabel(m.t);
    if (d !== lastDay) { html += `<div class="day-sep"><span>${d}</span></div>`; lastDay = d; }
    const dirCls = m.dir === 'out' ? 'out' : 'in';
    const kindCls = ' k-' + (m.kind || 'text');
    const parts = [];
    if (m.input) parts.push(`<span class="tk">入 ${nfmt(m.input, 1)}</span>`);
    if (m.output) parts.push(`<span class="tk">出 ${nfmt(m.output, 1)}</span>`);
    if (m.reasoning) parts.push(`<span class="tk">思考 ${nfmt(m.reasoning, 1)}</span>`);
    parts.push(`<span class="cost">${money(m.cost, 4)}</span>`);
    parts.push(`<span>${hhmm(m.t)}</span>`);
    html += `<div class="msg ${dirCls}${kindCls}">
      <div class="msg-av" style="background:${dirCls === 'out' ? 'linear-gradient(135deg,var(--green),var(--green-d))' : a.bg}">
        ${dirCls === 'out' ? '我' : escapeHtml(m.sender ? String(m.sender).slice(0, 2) : a.txt)}</div>
      <div class="msg-col">
        ${senderLabel(m)}
        <div class="bubble">${escapeHtml(m.text)}</div>
        <div class="msg-footer">${parts.join('')}</div>
      </div></div>`;
  }
  body.innerHTML = html;
  if (keepScroll && !wasAtBottom) body.scrollTop = prevTop;
  else body.scrollTop = body.scrollHeight;

  // 底部费用条
  const tin = h.msgs.reduce((s, m) => s + (m.input || 0), 0);
  const tout = h.msgs.reduce((s, m) => s + (m.output || 0), 0);
  const th = h.msgs.reduce((s, m) => s + (m.reasoning || 0), 0);
  const cache = h.msgs.reduce((s, m) => s + (m.cache_hit || 0), 0);
  const nIn = h.msgs.filter(m => m.dir === 'in').length, nOut = h.msgs.length - nIn;
  $('#costStrip').innerHTML = `
    <span class="in">输入 <b>${nfmt(tin)}</b> tokens（对方 ${nIn} 条）</span>
    <span class="out">输出 <b>${nfmt(tout)}</b> tokens（我 ${nOut} 条）</span>
    <span class="think">思考 <b>${nfmt(th)}</b> tokens</span>
    <span>缓存命中 <b>${nfmt(cache)}</b></span>
    <span class="money">这段对话值 <b>${money(h.cost, 3)}</b></span>`;
}

/* ---------------------------- 额度与 Key ---------------------------- */

function renderBilling() {
  const b = state.billing; if (!b) return;
  $('#blQuota').textContent = nfmt(b.total_quota, 2);
  $('#quotaInput').value = b.total_quota;
  $('#blSpent').textContent = nfmt(b.total_spent, 2);
  $('#blPct').textContent = ((b.used_ratio || 0) * 100).toFixed(1) + '%';
  $('#blRemain').textContent = nfmt(b.remaining, 2);
  const bar = $('#blBar');
  bar.style.width = clamp((b.used_ratio || 0) * 100, 0, 100) + '%';
  bar.className = 'bar-fill' + ((b.used_ratio || 0) > 0.8 ? ' warn' : '');

  const models = state.models.length ? state.models : (b.models || []);
  const activeKey = (b.config || {}).active_key;
  const el = $('#keyList');
  if (!b.keys || !b.keys.length) { el.innerHTML = '<div class="muted tiny">还没有 Key，点右上角新建一个</div>'; }
  else el.innerHTML = b.keys.map(k => {
    const pct = clamp(k.used_ratio * 100, 0, 100);
    const cls = k.used_ratio > 1 ? 'over' : (k.used_ratio > 0.8 ? 'warn' : '');
    const isActive = k.id === activeKey;
    return `<div class="key-row${isActive ? ' is-active' : ''}">
      <div>
        <div class="key-label">${escapeHtml(k.label)}
          ${isActive ? '<span class="badge">当前计价</span>' : ''}</div>
        <div class="key-mask">${escapeHtml(k.masked || '')}</div>
      </div>
      <div><div class="tiny muted">已用</div><div style="color:var(--amber);font-variant-numeric:tabular-nums">${money(k.used, 3)}</div></div>
      <div>
        <div class="tiny muted">额度 ${money2(k.quota)} · 剩 ${money2(k.remaining)}</div>
        <div class="key-bar"><div class="${cls}" style="width:${pct}%"></div></div>
      </div>
      <div><select class="sel" data-model-for="${k.id}">
        ${models.map(m => `<option value="${m.id}"${m.id === k.model ? ' selected' : ''}>${escapeHtml(m.name)}</option>`).join('')}
      </select></div>
      <div style="display:flex;gap:6px">
        <button class="btn small ghost" data-set-quota="${k.id}">改额度</button>
        ${isActive ? '' : `<button class="btn small ghost" data-activate="${k.id}">用于计价</button>`}
        <button class="btn small danger" data-del="${k.id}">删</button>
      </div></div>`;
  }).join('');

  // 事件绑定
  $$('[data-del]', el).forEach(b2 => b2.onclick = async () => {
    if (!confirm('删除这个 Key？')) return;
    const r = await post('/api/keys/delete', { id: b2.dataset.del });
    state.billing = r.billing; renderBilling(); toast('已删除');
  });
  $$('[data-activate]', el).forEach(b2 => b2.onclick = async () => {
    const r = await post('/api/active', { key_id: b2.dataset.activate });
    state.overview = r.overview; await loadAll(false); toast('已切换计价 Key');
  });
  $$('[data-set-quota]', el).forEach(b2 => b2.onclick = async () => {
    const k = (state.billing.keys || []).find(x => x.id === b2.dataset.setQuota);
    const v = prompt(`「${k.label}」的额度（元）`, k.quota);
    if (v === null) return;
    const r = await post('/api/keys/update', { id: k.id, quota: Number(v) });
    state.billing = r.billing;
    state.overview = await api('/api/overview');
    renderBilling(); renderOverview(); toast('额度已更新');
  });
  $$('[data-model-for]', el).forEach(sel => sel.onchange = async () => {
    const r = await post('/api/keys/update', { id: sel.dataset.modelFor, model: sel.value });
    state.billing = r.billing; toast('该 Key 的模型已切换');
  });

  // 按模型消费
  const mu = b.by_model || {};
  const ids = Object.keys(mu);
  const maxCost = Math.max(...ids.map(i => mu[i].cost), 0.000001);
  const mol = $('#modelUsage');
  mol.innerHTML = ids.length ? ids.map(id => {
    const row = mu[id];
    return `<div class="mu-row">
      <div class="mu-name">${escapeHtml(id)}</div>
      <div class="mu-bar"><div style="width:${(row.cost / maxCost) * 100}%"></div></div>
      <div class="mu-val">${money(row.cost, 3)} · ${row.n} 条</div></div>`;
  }).join('') : '<div class="muted tiny">还没有消费记录</div>';
}

/* ---------------------------- 模型广场 ---------------------------- */

function renderMarket() {
  const el = $('#modelGrid');
  const q = ($('#mkSearch').value || '').toLowerCase();
  const models = state.models.filter(m => !q ||
    (m.name || '').toLowerCase().includes(q) || (m.vendor || '').toLowerCase().includes(q));
  $('#mkCount').textContent = state.models.length + ' 个模型';
  const activeModel = (state.billing && state.billing.config || {}).active_model;
  el.innerHTML = models.map(m => {
    const on = m.id === activeModel;
    const a = avatar(m.vendor || m.name);
    return `<div class="model-card${on ? ' on' : ''}">
      <div class="mc-top">
        <div class="mc-logo" style="background:${a.bg}">${escapeHtml((m.vendor || '?')[0])}</div>
        <div style="flex:1;min-width:0">
          <div class="mc-name">${escapeHtml(m.name)}</div>
          <div class="mc-vendor">${escapeHtml(m.vendor || '自定义')} · ${escapeHtml(m.context || '')}</div>
        </div>
        ${m.tag ? `<div class="mc-tag">${escapeHtml(m.tag)}</div>` : ''}
      </div>
      <div class="mc-note">${escapeHtml(m.note || '')}</div>
      <div class="price-grid">
        <div class="price-cell hl"><em>输入 / 百万</em><b>¥${m.input}</b></div>
        <div class="price-cell hl"><em>输出 / 百万</em><b>¥${m.output}</b></div>
        <div class="price-cell"><em>缓存 / 百万</em><b>¥${m.cache ?? 0}</b></div>
        <div class="price-cell"><em>思考倍率</em><b>${m.reasoning_mult ?? 1}×</b></div>
      </div>
      <div class="mc-foot">
        <button class="btn small ${on ? 'green' : ''}" data-use="${m.id}">${on ? '正在使用' : '用它计价'}</button>
        <button class="btn small ghost" data-price="${m.id}">改价</button>
        ${m.custom ? `<button class="btn small danger" data-rm="${m.id}">删</button>` : ''}
      </div></div>`;
  }).join('') || '<div class="empty" style="height:160px">没有匹配的模型</div>';

  $$('[data-use]', el).forEach(b => b.onclick = async () => {
    const r = await post('/api/active', { model: b.dataset.use });
    state.overview = r.overview;
    state.billing = await api('/api/billing');
    renderMarket(); renderOverview(); renderBilling();
    toast('已切换计价模型');
  });
  $$('[data-price]', el).forEach(b => b.onclick = async () => {
    const m = state.models.find(x => x.id === b.dataset.price);
    const i = prompt(`「${m.name}」输入单价（元/百万）`, m.input); if (i === null) return;
    const o = prompt('输出单价（元/百万）', m.output); if (o === null) return;
    const c = prompt('缓存单价（元/百万）', m.cache ?? 0); if (c === null) return;
    const r = await post('/api/models/price', { id: m.id, price: { input: +i, output: +o, cache: +c } });
    state.models = r.models;
    state.overview = await api('/api/overview');
    state.billing = await api('/api/billing');
    renderMarket(); renderOverview(); renderBilling(); toast('价格已更新，全站立即按新价重算');
  });
  $$('[data-rm]', el).forEach(b => b.onclick = async () => {
    if (!confirm('删除这个自定义模型？')) return;
    const r = await post('/api/models/delete', { id: b.dataset.rm });
    state.models = r.models; renderMarket(); toast('已删除');
  });
}

/* ---------------------------- Token 实验室 ---------------------------- */

function runLab() {
  const text = $('#labInput').value || '';
  const m = (state.overview && state.overview.model) || { name: '—', input: 0, output: 0, cache: 0 };
  $('#labModel').textContent = m.name;
  if (!text.trim()) { $('#labResult').innerHTML = '<div class="muted tiny">输入点文字看看</div>'; return; }

  // 前端复刻一份估算逻辑，做到输入即时反馈（不必每次请求后端）
  const chk = (re) => (text.match(re) || []).length;
  const emoji = chk(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}]/gu);
  const cjk = chk(/[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]/g);
  const words = chk(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g);
  const digits = chk(/\d+(?:[,.]\d+)*/g);
  const punct = chk(/[\p{P}\p{S}]/gu) - 0;
  const raw = cjk / 0.6 + words * 1.25 + digits / 3 + emoji * 2.5 + Math.max(punct, 0) / 3;
  const body = raw + 4;

  const KIND = [[/^[\s]*(嗯|哦|好|好的|哈哈+|在|？|\?|ok|收到|行)[\s]*$/i, '短确认', 0.12],
                [/error|exception|traceback|报错|失败|异常/i, '报错', 2.2],
                [/```|def |class |function |import |<\/?[a-z]+>/i, '代码', 2.0],
                [/https?:\/\/|www\./i, '链接', 1.8],
                [/[?？]$|怎么|为什么|how|what|why/i, '疑问', 1.5]];
  let density = 1.0, kind = '普通';
  if (emoji > 0 && cjk + words + digits === 0) { kind = '纯表情'; density = 0.15; }
  else for (const [re, k, d] of KIND) { if (re.test(text)) { kind = k; density = d; break; } }

  const think = Math.min(6 + 1.35 * body * density, 4000);
  const cIn = (body * (1 - 0.65)) / 1e6 * m.input + (body * 0.65) / 1e6 * (m.cache ?? 0);
  const cOut = body / 1e6 * m.output;
  const cThink = think / 1e6 * m.output * (m.reasoning_mult ?? 1);

  $('#labResult').innerHTML = `
    <div class="lr-row"><span>内容类型</span><b style="font-size:13px">${kind}（密度 ${density}）</b></div>
    <div class="lr-row"><span>字符构成</span><b style="font-size:13px">汉字 ${cjk} · 词 ${words} · 数字 ${digits} · 表情 ${emoji}</b></div>
    <div class="lr-row"><span>作为「输入」（别人说的）</span><b class="c-in">${body.toFixed(1)} tokens</b></div>
    <div class="lr-row"><span>作为「输出」（我说的）</span><b class="c-out">${body.toFixed(1)} tokens</b></div>
    <div class="lr-row"><span>思考 token</span><b class="c-think">${think.toFixed(1)}</b></div>
    <div class="lr-row"><span>按输入计价</span><b class="c-money">¥${cIn.toFixed(6)}</b></div>
    <div class="lr-row"><span>按输出计价</span><b class="c-money">¥${cOut.toFixed(6)}</b></div>
    <div class="lr-row"><span>含思考合计（输出侧）</span><b class="c-money">¥${(cOut + cThink).toFixed(6)}</b></div>
    <div class="lr-row"><span>这条消息占额度</span>
      <b style="color:var(--txt)">${((cOut + cThink) / (state.billing?.total_quota || 1) * 100).toExponential(2)}%</b></div>`;
}

/* ---------------------------- 给会话塞 Key ---------------------------- */

function openKeyModal() {
  const h = state.history;
  if (!h) return toast('先选一个会话', 'err');
  const keys = (state.billing && state.billing.keys) || [];
  const pv = h.person || {};
  $('#keyModalSub').textContent = `${h.name} · ${h.n} 条消息 · 当前累计 ${money(h.cost, 3)}`;

  const rows = [];
  rows.push(`<div class="key-opt${!pv.key_id ? ' on' : ''}" data-key="">
    <div class="ko-radio"></div>
    <div class="ko-mid">
      <div class="ko-name">不单独分配（跟随全局）</div>
      <div class="ko-sub">用当前全局模型 ${escapeHtml((state.overview && state.overview.model || {}).name || '')} 计价</div>
    </div>
  </div>`);

  for (const k of keys) {
    const m = (state.models || []).find(x => x.id === k.model) || {};
    const on = pv.key_id === k.id;
    rows.push(`<div class="key-opt${on ? ' on' : ''}" data-key="${k.id}">
      <div class="ko-radio"></div>
      <div class="ko-mid">
        <div class="ko-name">${escapeHtml(k.label)}</div>
        <div class="ko-sub">${escapeHtml(m.name || k.model || '未指定模型')} · 额度 ${money2(k.quota)} · 已用 ${money(k.used, 3)}</div>
      </div>
      <div class="ko-right"><b>¥${m.input ?? '—'}</b>入 / ¥${m.output ?? '—'} 出</div>
    </div>`);
  }
  $('#keyPick').innerHTML = rows.join('');

  const close = () => $('#keyModal').classList.remove('on');
  $$('#keyPick .key-opt').forEach(el => el.onclick = async () => {
    const keyId = el.dataset.key || null;
    close();
    await assignPersonKey(h.wxid, keyId);
  });
  $('#keyModalClose').onclick = close;
  $('#keyModalMarket').onclick = () => { close(); setView('market'); };
  $('#keyModal').onclick = e => { if (e.target.id === 'keyModal') close(); };
  $('#keyModal').classList.add('on');
}

async function assignPersonKey(wxid, keyId) {
  try {
    const r = await post('/api/person/assign', { wxid, key_id: keyId });
    state.billing = r.billing;
    await loadAll(false);
    await openChat(wxid, true);
    const nm = r.person && r.person.key_label;
    toast(keyId ? `已给这个会话塞入 Key「${nm}」，历史已按新模型重算`
                : '已取消单独分配，恢复跟随全局模型');
  } catch (e) {
    toast('分配失败：' + e.message, 'err');
  }
}

/* ---------------------------- 拟回复（只生成，不发送） ---------------------------- */

async function genDraft() {
  const h = state.history;
  if (!h) return toast('先选一个会话', 'err');
  const list = $('#draftList');
  list.innerHTML = '<span class="muted tiny">生成中…</span>';
  try {
    const r = await post('/api/draft', { wxid: h.wxid, topic: $('#draftTopic').value || '' });
    if (r.error) { list.innerHTML = `<span class="muted tiny">${escapeHtml(r.error)}</span>`; return; }
    renderDrafts(r);
  } catch (e) {
    list.innerHTML = `<span class="muted tiny">生成失败：${escapeHtml(e.message)}</span>`;
  }
}

function renderDrafts(r) {
  const list = $('#draftList');
  const cands = r.candidates || [];
  const tag = r.source === 'model'
    ? `<span class="badge">AI 生成</span>`
    : `<span class="badge off">本地模板</span>`;
  if (!cands.length) {
    list.innerHTML = `<span class="muted tiny">${escapeHtml(r.note || '没有候选')}</span>`;
    return;
  }
  list.innerHTML = cands.map((c, i) => `
    <div class="draft-item" data-i="${i}" title="点击复制">
      <span class="di-text">${escapeHtml(c)}</span>
      <span class="di-len">${c.length} 字</span>
      <span class="di-copy">复制</span>
    </div>`).join('')
    + `<div class="draft-note">${tag} ${escapeHtml(r.note || '')}</div>`;

  $$('#draftList .draft-item').forEach(el => el.onclick = async () => {
    const text = cands[Number(el.dataset.i)];
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制，去微信里粘贴发送');
    } catch {
      // 剪贴板权限被拒时退回选中文本，让用户自己 Ctrl+C
      const r2 = document.createRange();
      r2.selectNodeContents(el.querySelector('.di-text'));
      const sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(r2);
      toast('已选中文字，按 Ctrl+C 复制', 'info');
    }
  });
}

async function openDraftCfg() {
  const c = await api('/api/draft/config');
  const base = prompt('模型接口 base_url（OpenAI 兼容，例如 https://api.deepseek.com/v1）\n留空则用本地模板', c.base_url || '');
  if (base === null) return;
  const key = prompt('API Key（只存在本机 draft.json，不会上传）', c.api_key || '');
  if (key === null) return;
  const model = prompt('模型名', c.model || 'deepseek-v4-flash');
  if (model === null) return;
  const extra = prompt('额外要求（可选，例如"别用波浪号"）', c.extra_instruction || '');
  await post('/api/draft/config', { base_url: base, api_key: key, model, extra_instruction: extra || '' });
  toast(base && key ? '已保存，下次生成会用模型' : '已保存（没配 key，仍用本地模板）');
}

/* ---------------------------- 数据加载 ---------------------------- */

async function loadAll(full = true) {
  const jobs = [
    api('/api/overview').then(r => state.overview = r),
    api('/api/conversations').then(r => state.convs = r),
    api('/api/billing').then(r => { state.billing = r; state.models = r.models || []; }),
    api('/api/breakdown').then(r => state.breakdown = r),
    api('/api/hours?hours=48').then(r => state.hours = r),
    api('/api/timeseries?days=' + state.days).then(r => state.timeseries = r),
  ];
  await Promise.all(jobs);
  renderOverview();
  if (full) { renderBilling(); renderMarket(); }
  if (state.view === 'chat') renderConvList();
}

function connectSSE() {
  const es = new EventSource('/api/stream');
  es.onopen = () => { state.live = true; setLive(true); };
  es.onerror = () => { state.live = false; setLive(false); };
  es.onmessage = (ev) => {
    setLive(true);
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (d.type === 'sync' && d.added) {
      toast(`实时入账 ${d.added} 条`, 'info', 2000);
      scheduleRefresh();
    } else if (d.type === 'sim') {
      scheduleRefresh();
    }
  };
}

/* 同步事件可能连续来（多条消息），不能每条都全量刷新一遍——
   那样会在你正在看/滚动时反复重渲染，感觉就是"卡"。
   这里合并成最多每 4 秒一次，并且：
     · 页面在后台时干脆不刷（回来再刷）
     · 打开的会话只在"最后一条消息变了"时才重新拉取，避免无意义重渲染 */
let refreshTimer = null, refreshPending = false;

function scheduleRefresh() {
  if (document.hidden) { refreshPending = true; return; }
  if (refreshTimer) return;
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    await loadAll(false);
    if (state.active) {
      const cur = state.convs.find(c => c.wxid === state.active);
      const shown = state.history && state.history.msgs.length
        ? state.history.msgs[state.history.msgs.length - 1].key : null;
      const lastKey = cur && cur.last_t ? `${cur.wxid}:${cur.last_t}` : null;
      // 只有确认有新内容才重拉，否则聊天区保持不动（不打断滚动）
      const known = state.history && state.history.last_key;
      if (cur && (!known || cur.last_t !== known)) {
        await openChat(state.active, true);
      }
    }
  }, 4000);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && refreshPending) {
    refreshPending = false;
    scheduleRefresh();
  }
});
function setLive(on) {
  const el = $('#liveBadge');
  el.className = 'live ' + (on ? 'on' : 'off');
  $('#liveTxt').textContent = on ? '实时监听中' : '连接断开';
}

/* ---------------------------- 初始化 ---------------------------- */

function debounce(fn, ms) {
  let t = null;
  return function (...args) {
    if (t) clearTimeout(t);
    t = setTimeout(() => { t = null; fn.apply(this, args); }, ms);
  };
}

function init() {
  $$('.nav-item').forEach(b => b.onclick = () => setView(b.dataset.view));
  // 搜索框防抖：每敲一个键都重建 158 个会话节点会明显发顿
  $('#chatSearch').oninput = debounce(e => { state.filter = e.target.value; renderConvList(); }, 160);
  $('#chatAvBtn').onclick = openKeyModal;
  $('#btnDraft').onclick = genDraft;
  $('#btnDraftCfg').onclick = openDraftCfg;
  $('#draftTopic').onkeydown = e => { if (e.key === 'Enter') genDraft(); };
  $('#mkSearch').oninput = debounce(() => renderMarket(), 160);
  // 实验室：输入实时算，但用 rAF 合并到每帧一次
  let labRaf = null;
  const labSchedule = () => {
    if (labRaf) return;
    labRaf = requestAnimationFrame(() => { labRaf = null; runLab(); });
  };
  $('#labInput').oninput = labSchedule;
  $$('[data-fill]').forEach(b => b.onclick = () => {
    $('#labInput').value = b.dataset.fill; labSchedule();
  });
  $('#tsSeg').onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    $$('#tsSeg button').forEach(x => x.classList.toggle('on', x === b));
    state.days = Number(b.dataset.days);
    api('/api/timeseries?days=' + state.days).then(r => { state.timeseries = r; renderChart(); });
  };
  $('#btnSync').onclick = async () => {
    toast('正在同步微信库…', 'info');
    const r = await post('/api/sync');
    if (r.ok) { toast(`同步完成，新增 ${r.added} 条`); await loadAll(false); }
    else toast('同步失败：' + (r.error || ''), 'err', 4000);
  };
  $('#btnSim').onclick = async () => {
    toast('跑 20 轮使用模拟…', 'info');
    const r = await post('/api/simulate', { rounds: 20 });
    toast(`模拟完成，写入 ${r.added} 条`);
    await loadAll(true);
  };
  $('#btnSaveQuota').onclick = async () => {
    const v = Number($('#quotaInput').value);
    if (!v || v <= 0) return toast('请输入有效额度', 'err');
    const r = await post('/api/config/quota', { total_quota: v });
    state.billing = r; state.overview = await api('/api/overview');
    renderBilling(); renderOverview(); toast('总额度已更新为 ' + money2(v));
  };
  $('#btnAddKey').onclick = async () => {
    const label = prompt('这个 Key 叫什么？', '新额度'); if (label === null) return;
    const quota = prompt('分配多少额度（元）？', '100'); if (quota === null) return;
    const model = prompt('用哪个模型计价？填模型 id（可留空）', (state.billing.config || {}).active_model || '');
    const r = await post('/api/keys', { label, quota: Number(quota), model: model || '' });
    state.billing = r.billing;
    state.overview = await api('/api/overview');
    renderBilling(); renderOverview();
    toast(r.over_allocated ? '已创建（注意：分配额度已超过总额度）' : '已创建', r.over_allocated ? 'info' : 'ok');
  };
  $('#btnAddModel').onclick = async () => {
    const name = prompt('模型名称', 'my-model'); if (name === null) return;
    const vendor = prompt('供应商', '自定义') || '自定义';
    const input = prompt('输入单价（元/百万 tokens）', '1'); if (input === null) return;
    const output = prompt('输出单价（元/百万 tokens）', '4'); if (output === null) return;
    const cache = prompt('缓存单价（元/百万 tokens）', '0.1'); if (cache === null) return;
    const r = await post('/api/models/custom', { model: { name, vendor, input: +input, output: +output, cache: +cache, reasoning_mult: 1 } });
    state.models = r.models; renderMarket(); toast('自定义模型已加入广场');
  };
  $('#labInput').oninput = runLab;
  $$('[data-fill]').forEach(b => b.onclick = () => { $('#labInput').value = b.dataset.fill; runLab(); });

  loadAll(true).then(() => {
    connectSSE();
    if (!state.convs.length) toast('还没有数据，点左下角「模拟 20 轮」先看看效果', 'info', 6000);
  }).catch(e => toast('加载失败：' + e.message, 'err', 5000));
}

document.addEventListener('DOMContentLoaded', init);
