# -*- coding: utf-8 -*-
"""wcpage.py — 统一洞察页面的前端（内联单页应用）

交互设计考量：
  · 顶部统一「选人」——三个功能共用，不用各自再选一次
  · 大结果不一次渲染：事件列表先给 60 条 + 「加载更多」
  · 长任务（抽事件/跑业务线）先出预估，用户确认再跑，跑时有进度条
  · 点事件就地展开上下文，不跳页、不弹窗
"""

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>微信洞察</title>
<style>
__CSS__
.wrap{max-width:1200px;margin:0 auto;padding:0 22px 70px;position:relative;z-index:1}
/* 顶部选人 */
.toolbar{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.94);
  border-bottom:1px solid var(--line);padding:12px 22px}
.toolbar-in{max-width:1200px;margin:0 auto;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;flex:0 0 auto}
.brand .logo{width:32px;height:32px;border-radius:10px;display:grid;place-items:center;
  font-size:17px;background:linear-gradient(135deg,var(--green),#0a9ad6)}
.brand b{font-size:15px}
.brand span{font-size:10.5px;color:var(--txt-3);display:block}
/* 人员选择 */
.picker{position:relative;flex:1;min-width:240px;max-width:400px}
.picker input{width:100%;background:var(--surface);border:1px solid var(--line-2);
  border-radius:10px;padding:9px 13px;font-size:13px;outline:none}
.picker input:focus{border-color:var(--blue)}
.tbl-wrap{overflow:auto;border-radius:var(--r-s);border:1px solid var(--line)}
.tbl th{white-space:nowrap}
.tbl td{min-width:64px}
.tpl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(252px,1fr));gap:12px}
.tpl-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);
  padding:14px;cursor:pointer;transition:border-color .15s,transform .15s;
  display:flex;flex-direction:column;gap:8px}
.tpl-card:hover{border-color:var(--line-2);transform:translateY(-2px)}
.tpl-card.on{border-color:rgba(7,193,96,.55)}
.tpl-top{display:flex;align-items:center;gap:9px}
.tpl-ico{width:32px;height:32px;border-radius:9px;display:grid;place-items:center;
  font-size:16px;background:var(--surface-2);border:1px solid var(--line)}
.tpl-nm{font-size:13.5px;font-weight:620}
.tpl-desc{font-size:11.5px;color:var(--txt-3);line-height:1.5;min-height:32px}
.tpl-fields{display:flex;flex-wrap:wrap;gap:4px}
.tpl-fields .tag{font-size:10px}
.plist{position:absolute;top:calc(100% + 5px);left:0;right:0;max-height:340px;overflow:auto;
  background:var(--surface);border:1px solid var(--line-2);border-radius:11px;
  box-shadow:var(--shadow-l);display:none;z-index:40}
.plist.on{display:block}
.pitem{display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;font-size:13px}
.pitem:hover,.pitem.sel{background:rgba(77,124,254,.14)}
.pitem .av{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;
  font-size:12px;font-weight:600;color:#fff;flex:0 0 auto}
.pitem .nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pitem .mt{font-size:10.5px;color:var(--txt-3);flex:0 0 auto}
.who{display:flex;align-items:center;gap:9px;flex:0 0 auto;font-size:13px;
  padding:7px 13px;background:rgba(7,193,96,.1);border:1px solid rgba(7,193,96,.3);
  border-radius:99px}
.who .dot{width:7px;height:7px;border-radius:50%;background:var(--green)}
/* 标签 */
.tabs{display:flex;gap:2px;background:var(--surface-2);padding:3px;border-radius:9px;border:1px solid var(--line)}
.tabs button{padding:7px 16px;border-radius:8px;font-size:13px;color:var(--txt-2);
  transition:.15s;white-space:nowrap}
.tabs button:hover{color:var(--txt)}
.tabs button.on{background:var(--surface);color:var(--txt);box-shadow:var(--shadow-s)}
.view{display:none;padding-top:20px}
.view.on{display:block;animation:fade .22s ease}
@keyframes fade{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
/* 周期选择 */
.chips{display:flex;gap:5px;flex-wrap:wrap}
.chips button{padding:6px 13px;border-radius:8px;font-size:12.5px;color:var(--txt-2);
  border:1px solid var(--line-2);background:var(--surface)}
.chips button:hover{border-color:var(--line-2);color:var(--txt)}
.chips button.on{background:var(--txt);color:#fff;
  border-color:transparent}
/* 事件 */
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.fchip{padding:6px 12px;border-radius:99px;font-size:12px;color:var(--txt-2);
  border:1px solid var(--line-2);background:var(--surface);cursor:pointer;
  display:flex;align-items:center;gap:5px}
.fchip:hover{border-color:var(--line-3);color:var(--txt)}
.fchip.on{background:var(--accent-soft);border-color:#c9dcff;color:var(--accent-d);font-weight:600}
.fchip .n{font-size:10.5px;color:var(--txt-3);font-variant-numeric:tabular-nums}
.fchip.on .n{color:var(--blue-2)}
.ev{display:flex;flex-direction:column;gap:8px}
.ecard{background:var(--surface);border:1px solid var(--line);
  border-radius:var(--r-s);padding:12px 14px;cursor:pointer;transition:border-color .13s}
.ecard:hover{border-color:var(--line-3)}
.ecard.open{border-color:#bcd3ff;background:#f8fbff}
.ecard .h{display:flex;align-items:center;gap:9px;margin-bottom:5px}
.ecard .ic{font-size:15px;flex:0 0 auto}
.ecard .lb{font-size:11px;padding:2px 8px;border-radius:99px;
  background:var(--surface-2);border:1px solid var(--line);color:var(--txt-2);flex:0 0 auto}
.ecard .dt{font-size:11px;color:var(--txt-3);flex:0 0 auto;margin-left:auto;
  font-variant-numeric:tabular-nums}
.ecard .sm{font-size:13.5px;line-height:1.55}
.ecard .qt{font-size:12px;color:var(--txt-2);margin-top:6px;padding-left:10px;
  border-left:2px solid var(--line-2)}
.ctx{display:flex;flex-direction:column;gap:3px;margin-top:10px;font-size:12px;
  max-height:300px;overflow:auto}
.ctx .r{padding:5px 9px;border-radius:6px;display:flex;gap:9px;
  background:var(--surface-2)}
.ctx .r.hit{background:var(--accent-soft);border:1px solid #cfe0ff}
.ctx .w{color:var(--txt-3);flex:0 0 auto;font-size:11px}
.ctx .t{flex:1;word-break:break-word}
/* 分析结果 */
.ana{font-size:13.5px;line-height:1.78}
.ana h3{font-size:14.5px;margin:16px 0 8px;color:var(--blue-2)}
.ana h4{font-size:13.5px;margin:12px 0 6px}
.ana p{margin:6px 0}
.ana ul{margin:6px 0 6px 18px;padding:0}
.ana li{margin:4px 0}
.ana b{color:var(--txt)}
.ana .ref{color:var(--blue-2)}
.ana code{font-family:var(--mono);font-size:12px;background:var(--surface-2);
  padding:1px 5px;border-radius:5px}
/* 进度 */
.prog{height:5px;border-radius:5px;background:var(--surface-2);
  overflow:hidden;margin-top:10px;display:none}
.prog.on{display:block}
.prog i{display:block;height:100%;width:30%;border-radius:5px;
  background:linear-gradient(90deg,var(--green),var(--cyan));animation:slide 1.4s infinite}
@keyframes slide{0%{margin-left:-30%}100%{margin-left:100%}}
.loading{display:flex;align-items:center;gap:10px;color:var(--txt-2);font-size:13px;padding:14px 0}
.spin{width:15px;height:15px;border:2px solid var(--line-2);
  border-top-color:var(--blue);border-radius:50%;animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.rowflex{display:flex;align-items:center;gap:10px;flex-wrap:wrap}

/* 年度评语 */
.comments{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.cmt{display:flex;gap:11px;padding:14px;border-radius:var(--r-s);
  background:linear-gradient(135deg,#fcfdff,#f7faff);
  border:1px solid var(--line)}
.cmt .ci{font-size:21px;line-height:1.15;flex:0 0 auto}
.cmt .ct{font-size:13px;font-weight:640;margin-bottom:4px;color:var(--txt)}
.cmt .cs{font-size:12px;color:var(--txt-2);line-height:1.65}

/* 选人区（在"聊天与关系"页内） */
.pickrow{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.quick{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:12px}
.qp{display:inline-flex;align-items:center;gap:6px;padding:5px 10px 5px 5px;
  border-radius:99px;border:1px solid var(--line);background:var(--surface);
  font-size:12.5px;color:var(--txt-2);transition:border-color .12s,background .12s}
.qp:hover{border-color:var(--line-3);color:var(--txt)}
.qp.on{border-color:#bcd3ff;background:var(--accent-soft);color:var(--accent-d);
  font-weight:600}
.qp em{font-style:normal;font-size:10.5px;color:var(--txt-4)}
.who-x{border:0;background:none;color:var(--txt-3);cursor:pointer;font-size:11px;
  padding:0 2px;line-height:1}
.who-x:hover{color:var(--rose)}
/* 引导条 */
.guide{margin-top:14px;padding:11px 13px;border-radius:var(--r-s);
  background:linear-gradient(135deg,#f7faff,#f2f8ff);
  border:1px solid #dfeafd;font-size:12.5px;color:var(--txt-2);line-height:1.85}
.guide b{color:var(--accent-d);margin-right:8px;font-weight:600}
.guide span{display:block}

/* ---------------- 多选下拉（业务线范围） ---------------- */
.msel{position:relative}
.msel-btn{padding:7px 12px;border-radius:var(--r-s);border:1px solid var(--line-2);
  background:var(--surface);font-size:12.5px;color:var(--txt-2);
  display:inline-flex;align-items:center;gap:7px}
.msel-btn:hover{border-color:var(--line-3);color:var(--txt)}
.msel-n{font-size:10.5px;color:var(--accent-d);background:var(--accent-soft);
  border:1px solid #d6e4ff;border-radius:99px;padding:1px 7px;font-weight:600}
.msel-pop{position:absolute;top:calc(100% + 6px);left:0;width:300px;z-index:40;
  background:var(--surface);border:1px solid var(--line-2);border-radius:var(--r);
  box-shadow:var(--shadow-l);padding:10px;display:none}
.msel-pop.on{display:block}
.msel-pop input{width:100%;margin-bottom:8px}
.msel-tools{display:flex;gap:5px;margin-bottom:8px}
.msel-tools button{font-size:11px;padding:4px 9px;border-radius:6px;
  border:1px solid var(--line);background:var(--surface-2);color:var(--txt-2)}
.msel-tools button:hover{border-color:var(--line-3);color:var(--txt)}
.msel-list{max-height:280px;overflow:auto;display:flex;flex-direction:column;gap:2px}
.msel-item{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;
  font-size:12.5px;cursor:pointer}
.msel-item:hover{background:var(--surface-2)}
.msel-item input{width:14px;height:14px;margin:0;flex:0 0 auto}
.msel-item .nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.msel-item .mt{font-size:10.5px;color:var(--txt-4);flex:0 0 auto}
.msel-item .k{font-size:10px;padding:1px 6px;border-radius:99px;flex:0 0 auto;
  background:var(--surface-2);color:var(--txt-3);border:1px solid var(--line)}
select{background:var(--surface);border:1px solid var(--line-2);
  border-radius:8px;padding:7px 10px;font-size:12.5px;outline:none}
select:focus{border-color:var(--accent)}

/* ---------------- 业务线：状态条 + 多条件筛选面板 ---------------- */
.statusbar{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:13px;
  padding-bottom:13px;border-bottom:1px solid var(--line)}
.stchip{padding:6px 12px;border-radius:99px;font-size:12px;color:var(--txt-2);
  border:1px solid var(--line-2);background:var(--surface);
  display:inline-flex;align-items:center;gap:6px}
.stchip:hover{border-color:var(--line-3);color:var(--txt)}
.stchip b{font-variant-numeric:tabular-nums;color:var(--txt-3);font-weight:600}
.stchip.on{background:var(--accent-soft);border-color:#c9dcff;color:var(--accent-d)}
.stchip.on b{color:var(--accent-d)}

.fpanel{background:var(--surface-2);border:1px solid var(--line);
  border-radius:var(--r-s);padding:13px;margin-bottom:13px;display:flex;
  flex-direction:column;gap:10px}
.frow2{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.fsearch{display:flex;align-items:center;gap:10px;flex:1;min-width:260px}
.fsearch input{flex:1}
.fmeta{font-size:11.5px;color:var(--txt-3);line-height:1.7}
.fmeta .syn{color:var(--accent-d);font-weight:500}
.fchips{display:flex;flex-direction:column;gap:7px}
.facet{display:flex;align-items:flex-start;gap:9px}
.facet-l{font-size:11.5px;color:var(--txt-3);width:66px;flex:0 0 auto;
  padding-top:4px;text-align:right}
.facet-v{display:flex;gap:5px;flex-wrap:wrap;flex:1}
.fchip2{padding:4px 10px;border-radius:99px;font-size:11.5px;color:var(--txt-2);
  border:1px solid var(--line-2);background:var(--surface);
  display:inline-flex;align-items:center;gap:5px}
.fchip2:hover{border-color:var(--line-3);color:var(--txt)}
.fchip2 em{font-style:normal;font-size:10px;color:var(--txt-4)}
.fchip2.on{background:var(--accent-soft);border-color:#c9dcff;color:var(--accent-d);
  font-weight:600}
.fchip2.on em{color:var(--accent-d)}
#ppCriteria:not(:empty){padding-top:2px;border-top:1px dashed var(--line);
  display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.pp-st{font-size:11.5px;padding:3px 6px;border-radius:6px;
  border:1px solid var(--line-2);background:var(--surface)}
.pp-star{width:14px;height:14px;cursor:pointer}
tr.ro-dim td{opacity:.48}
.qt-mini{font-size:10px;color:var(--txt-4)}

/* ══════ 年度报告：要有仪式感，不是统计表 ══════ */
.hero{
  position:relative;border-radius:18px;padding:38px 34px;overflow:hidden;
  background:linear-gradient(135deg,#ffffff 0%,#f7faff 55%,#f2f8ff 100%);
  border:1px solid var(--line);
}
.hero::after{
  content:"";position:absolute;inset:0;pointer-events:none;
  background-image:radial-gradient(560px 320px at 84% 6%,rgba(18,161,80,.10),transparent 62%),
                   radial-gradient(480px 320px at 6% 98%,rgba(47,111,235,.09),transparent 60%);
}
.hero>*{position:relative;z-index:1}
.hero .tagline{font-size:11.5px;color:var(--txt-3);letter-spacing:2.2px;
  text-transform:uppercase}
.hero h2{margin:12px 0 6px;font-size:27px;font-weight:700;letter-spacing:-.5px;
  background:linear-gradient(120deg,#1f2328,#2f6feb 55%,#0e9ba8);
  -webkit-background-clip:text;background-clip:text;color:transparent}
.hero .sub{font-size:13px;color:var(--txt-3)}
.hero .huge{display:flex;align-items:baseline;gap:12px;margin:22px 0 4px;flex-wrap:wrap}
.hero .huge b{font-size:56px;font-weight:800;letter-spacing:-3px;line-height:1;
  font-variant-numeric:tabular-nums;color:var(--txt)}
.hero .huge span{font-size:14px;color:var(--txt-3)}
.herostats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:14px;margin-top:26px}
.herostats div{background:rgba(255,255,255,.75);border:1px solid var(--line);
  border-radius:12px;padding:13px 15px}
.herostats .k{font-size:11px;color:var(--txt-3);margin-bottom:5px}
.herostats .v{font-size:19px;font-weight:680;letter-spacing:-.4px;color:var(--txt);
  font-variant-numeric:tabular-nums}

/* 称号 */
.titles{display:grid;grid-template-columns:repeat(auto-fill,minmax(214px,1fr));gap:12px}
.tcard{
  border-radius:14px;padding:16px;position:relative;overflow:hidden;
  background:var(--surface);
  border:1px solid var(--line);
}
.tcard::before{content:"";position:absolute;top:-28px;right:-16px;width:86px;height:86px;
  border-radius:50%;background:radial-gradient(circle,rgba(196,125,10,.13),transparent 68%)}
.tcard .ic{font-size:27px;line-height:1}
.tcard .nm{font-size:15.5px;font-weight:680;margin:9px 0 4px}
.tcard .ds{font-size:11.5px;color:var(--txt-2);line-height:1.5}

/* 口头禅 */
.phrases{display:flex;flex-wrap:wrap;gap:9px;align-items:baseline}
.phrases span{
  padding:7px 15px;border-radius:99px;font-weight:600;
  background:var(--accent-soft);border:1px solid #d6e4ff;color:var(--accent-d);
}
.phrases span.big{font-size:19px;padding:9px 20px}
.phrases span.mid{font-size:15px}
.phrases span small{font-weight:400;opacity:.6;margin-left:5px;font-size:10.5px}

/* 排行条 */
.rankbars{display:flex;flex-direction:column;gap:11px}
.rb{display:flex;align-items:center;gap:11px}
.rb .av{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;
  font-size:12.5px;font-weight:600;color:#fff;flex:0 0 auto}
.rb .mid{flex:1;min-width:0}
.rb .nm{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rb .tr{height:6px;border-radius:6px;background:var(--surface-2);margin-top:6px;
  overflow:hidden}
.rb .fl{height:100%;border-radius:7px}
.rb .vl{flex:0 0 auto;font-size:12.5px;font-variant-numeric:tabular-nums;
  color:var(--txt-2)}

/* 月度热力 */
.heat{display:grid;grid-template-columns:repeat(auto-fill,minmax(13px,1fr));gap:3px}
.heat i{aspect-ratio:1;border-radius:3px;display:block;background:var(--surface-2)}
.legend{display:flex;align-items:center;gap:6px;font-size:10.5px;color:var(--txt-3);
  margin-top:9px}
.legend i{width:11px;height:11px;border-radius:3px;display:block}

/* 一句话卡 */
.quip{
  border-radius:14px;padding:20px 22px;font-size:16px;line-height:1.6;font-weight:560;
  background:linear-gradient(135deg,rgba(7,193,96,.13),rgba(34,211,160,.06));
  border:1px solid rgba(7,193,96,.26);
}
.quip .em{color:var(--green)}
input[type=text],input[type=number]{background:var(--surface);border:1px solid var(--line-2);
  border-radius:8px;padding:7px 11px;font-size:12.5px;outline:none}
input:focus{border-color:var(--blue)}
</style></head>
<body>
<div class="bg-glow" aria-hidden="true"></div>

<div class="toolbar"><div class="toolbar-in">
  <div class="brand"><div class="logo">💬</div>
    <b>微信洞察<span>本地 · 只读</span></b></div>

  <div class="spacer" style="flex:1"></div>
  <div class="tabs" id="tabs">
    <button data-v="year" class="on">🎉 年度报告</button>
    <button data-v="people">👤 聊天与关系</button>
    <button data-v="consume">💸 聊天即消费</button>
    <button data-v="pipe">🏭 业务线</button>
  </div>
</div></div>

<div class="wrap">

  <!-- ═══════ 年度报告（全局） ═══════ -->
  <section class="view on" id="v-year">
    <div id="yrBody"><div class="card"><div class="loading">
      <span class="spin"></span>正在统计你的这一年…</div></div></div>
  </section>

  <!-- ═══════ 聊天与关系（合并：聊天报告 + 关系分析） ═══════ -->
  <section class="view" id="v-people">

    <!-- 选人：放在这个页面里，不常挂顶部 -->
    <div class="card">
      <div class="card-head"><span>看谁</span>
        <span class="hint">只列单聊；群聊不做个人分析</span></div>
      <div class="pickrow">
        <div class="picker" id="picker">
          <input id="q" placeholder="输入名字搜索…（也可以直接点下面的常用）" autocomplete="off">
          <div class="plist" id="plist"></div>
        </div>
        <div class="who" id="who" style="display:none">
          <span class="dot"></span><span id="whoName"></span>
          <button class="who-x" id="whoClear" title="取消选择">✕</button>
        </div>
      </div>
      <div class="quick" id="quickPick"></div>
      <div class="guide" id="peopleGuide">
        <b>怎么用</b>
        <span>① 上面选一个人 → ② 选时间范围 → ③ 下面自动出「聊天报告」；</span>
        <span>想看关系分析（谁先低头、有没有回避、关心对不对等），点「抽事件」。</span>
      </div>
    </div>

    <!-- 时间范围（统一，替代原来的一堆参数） -->
    <div class="card" id="rangeCard" style="display:none">
      <div class="card-head"><span>时间范围</span>
        <span class="hint" id="rpTitle"></span></div>
      <div class="rowflex">
        <div class="chips" id="periods">
          <button data-p="recent3">最近 3 天</button>
          <button data-p="week">本周</button>
          <button data-p="month" class="on">本月</button>
          <button data-p="quarter">本季度</button>
          <button data-p="year">今年</button>
          <button data-p="all">全部</button>
        </div>
        <div class="spacer" style="flex:1"></div>
        <button class="btn ghost small" id="rpExport">导出报告 HTML</button>
      </div>
    </div>

    <div id="rpBody"></div>

    <!-- 关系分析：事件驱动 -->
    <div class="card" id="evSetup" style="display:none">
      <div class="card-head"><span>关系分析 · 先抽事件</span>
        <span class="hint" id="evStat">还没抽取</span></div>
      <div class="rowflex">
        <button class="btn green" id="evExtract">抽取事件</button>
        <button class="btn ghost" id="evReload">刷新</button>
        <span class="tiny dim" id="evHint">把聊天拆成具体事件（冲突/关心/回避…），
          再用事件给结论。<b>已区分真吵架和玩闹式互怼</b></span>
      </div>
      <div class="prog" id="evProg"><i></i></div>
    </div>

    <div class="card" id="evCard" style="display:none">
      <div class="card-head"><span>挑事件看原文</span>
        <span class="rowflex">
          <label class="tiny dim">排序
            <select id="evSort">
              <option value="time">按时间（新→旧）</option>
              <option value="weight">按重要度</option>
            </select>
          </label>
          <span class="hint">点任意事件展开前后文对照</span>
        </span></div>
      <div class="filters" id="evFilters"></div>
      <div class="ev" id="evList"></div>
      <div style="text-align:center;margin-top:14px">
        <button class="btn ghost small" id="evMore" style="display:none">加载更多</button>
      </div>
    </div>

    <div class="card" id="anaCard" style="display:none">
      <div class="card-head"><span>基于事件给结论</span>
        <span class="hint">只会引用上面那些具体事件，样本不足会明说</span></div>
      <div class="rowflex">
        <button class="btn green" id="anaRun">生成分析</button>
        <span class="tiny dim" id="anaHint"></span>
      </div>
      <div class="prog" id="anaProg"><i></i></div>
      <div class="ana" id="anaBox" style="margin-top:14px"></div>
    </div>
  </section>

  <!-- ═══════ 已废弃的旧标签（保留 DOM 兼容，不显示） ═══════ -->
  <section class="view" id="v-report" style="display:none">
    <div class="card">
      <div class="card-head"><span>周期</span>
        <span class="hint" id="rpHint">先在上面选一个人</span></div>
      <div class="rowflex">
        <div class="chips" id="periods">
          <button data-p="week" class="on">本周</button>
          <button data-p="month">本月</button>
          <button data-p="quarter">本季度</button>
          <button data-p="year">今年</button>
          <button data-p="all">全部</button>
        </div>
        <div class="spacer" style="flex:1"></div>
        <button class="btn ghost small" id="rpExport">导出 HTML</button>
      </div>
    </div>
    <div id="rpBody"></div>
  </section>

  <!-- ═══════ 聊天即消费（完整版子应用，iframe 嵌入） ═══════ -->
  <section class="view" id="v-consume">
    <div id="csBody"></div>
  </section>

  <section class="view" id="v-relation" style="display:none">
    <div class="card">
      <div class="card-head"><span>第一步 · 把聊天拆成事件</span>
        <span class="hint" id="evStat">还没抽取</span></div>
      <div class="rowflex">
        <label class="tiny dim">时间范围
          <input type="number" id="evDays" value="0" min="0" style="width:70px">
          天（0=全部）</label>
        <label class="tiny dim">最多分析
          <input type="number" id="evChunks" value="120" min="10" max="400" style="width:74px">
          块</label>
        <button class="btn" id="evEstimate">先预估成本</button>
        <button class="btn green" id="evExtract">抽取事件</button>
        <span class="tiny dim" id="evHint"></span>
      </div>
      <div class="prog" id="evProg"><i></i></div>
    </div>

    <div class="card" id="evCard" style="display:none">
      <div class="card-head"><span>第二步 · 挑事件看原文</span>
        <span class="hint">点任意事件展开前后文对照</span></div>
      <div class="filters" id="evFilters"></div>
      <div class="ev" id="evList"></div>
      <div style="text-align:center;margin-top:14px">
        <button class="btn ghost small" id="evMore" style="display:none">加载更多</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><span>第三步 · 基于事件给结论</span>
        <span class="hint">只会引用上面那些具体事件</span></div>
      <div class="rowflex">
        <button class="btn green" id="anaRun">生成分析</button>
        <span class="tiny dim" id="anaHint">样本太少时会明说"看不出模式"，不会硬凑</span>
      </div>
      <div class="prog" id="anaProg"><i></i></div>
      <div class="ana" id="anaBox" style="margin-top:14px"></div>
    </div>
  </section>

  <!-- ═══════ 业务线 ═══════ -->
  <section class="view" id="v-pipe">
    <div class="card">
      <div class="card-head"><span>选择业务线</span>
        <span class="hint">这里<b>包含群聊</b>（群聊是业务线的主场景）</span></div>
      <div class="tpl-grid" id="tplGrid"></div>
    </div>
    <div class="card">
      <div class="card-head"><span>抽取范围</span></div>
      <div class="rowflex">
        <div class="msel" id="ppScope">
          <button class="msel-btn" id="ppScopeBtn">选择会话 <span class="msel-n" id="ppScopeN">全部</span> ▾</button>
          <div class="msel-pop" id="ppScopePop">
            <input type="text" id="ppScopeSearch" placeholder="筛选会话名…" autocomplete="off">
            <div class="msel-tools">
              <button data-pick="all">全选</button>
              <button data-pick="none">清空</button>
              <button data-pick="dm">只好友</button>
              <button data-pick="grp">只群聊</button>
            </div>
            <div class="msel-list" id="ppScopeList"></div>
          </div>
        </div>
        <label class="tiny dim">时间范围
          <select id="ppDays">
            <option value="7">最近 7 天</option>
            <option value="30">最近 30 天</option>
            <option value="90">最近 3 个月</option>
            <option value="180" selected>最近半年</option>
            <option value="0">全部</option>
          </select>
        </label>
        <button class="btn green" id="ppRun">开始抽取</button>
        <span class="tiny dim" id="ppHint"></span>
      </div>
      <div class="prog" id="ppProg"><i></i></div>
    </div>
    <div class="card">
      <div class="card-head"><span>结果</span>
        <span class="rowflex">
          <span class="tiny dim" id="ppSort">星标置顶 · 按时间倒序</span>
          <button class="btn ghost small" id="ppCsv">导出 CSV</button>
          <button class="btn ghost small" id="ppRefresh">刷新</button>
        </span></div>

      <!-- 状态计数（点一下即过滤） -->
      <div class="statusbar" id="ppStatusBar"></div>

      <!-- 多条件筛选面板（用户模拟里最痛的一条：单搜索框拼不出组合条件） -->
      <div class="fpanel">
        <div class="frow2">
          <div class="fsearch">
            <input type="text" id="ppSearch" autocomplete="off"
              placeholder="搜索…支持多词（空格分隔，全部命中）">
            <label class="tiny dim" title="开启后按同义词扩展，搜「兼职」也会带出「招人/日结/陪练」">
              <input type="checkbox" id="ppSem" checked> 语义扩展</label>
          </div>
          <div class="fmeta" id="ppFuzzyWrap">
            <label class="tiny dim"><input type="checkbox" id="ppFuzzy" checked> 模糊匹配</label>
          </div>
        </div>
        <div class="fmeta" id="ppSynHint"></div>
        <div class="fchips" id="ppFacets"></div>
        <div class="fmeta" id="ppCriteria"></div>
      </div>

      <div id="ppBody"><div class="empty"><span class="big-ico">🏭</span>还没有结果</div></div>
    </div>
  </section>

</div>
<script>
const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=(p,o)=>fetch(p,o).then(r=>r.json());
const post=(p,b)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(b||{})});
const S={contacts:[],chat:'',name:'',view:'year',period:'week',
  events:[],kinds:[],filter:'',shown:20,capped:[],pipelineTpl:'',tpls:[]};

/* ---------- 选人 ---------- */
function hue(s){let h=0;for(const c of (s||'?'))h=(h*31+c.charCodeAt(0))%360;return h;}
function av(n){const h=hue(n);return `<span class="av" style="background:linear-gradient(135deg,
  hsl(${h} 62% 52%),hsl(${(h+42)%360} 62% 42%))">${esc((n||'?')[0])}</span>`;}

function renderPicker(){
  const q=$('#q').value.trim().toLowerCase();
  const list=S.contacts.filter(c=>!q||c.name.toLowerCase().includes(q)).slice(0,60);
  $('#plist').innerHTML = list.length? list.map(c=>`
    <div class="pitem ${c.chat_id===S.chat?'sel':''}" data-id="${esc(c.chat_id)}"
         data-name="${esc(c.name)}">
      ${av(c.name)}<span class="nm">${esc(c.name)}</span>
      <span class="mt">${c.n.toLocaleString()} 条</span></div>`).join('')
    : '<div class="pitem dim">没有匹配的联系人</div>';
  $$('#plist .pitem[data-id]').forEach(el=>el.onclick=()=>{
    pickPerson(el.dataset.id, el.dataset.name);
  });
}

/* 选定一个人 */
function pickPerson(cid, name){
  S.chat=cid; S.name=name;
  $('#q').value=''; $('#plist').classList.remove('on');
  $('#who').style.display='flex'; $('#whoName').textContent=name;
  $$('#quickPick .qp').forEach(b=>b.classList.toggle('on', b.dataset.id===cid));
  loadPeople();
}
$('#whoClear').onclick=()=>{
  S.chat=''; S.name='';
  $('#who').style.display='none';
  $$('#quickPick .qp').forEach(b=>b.classList.remove('on'));
  showGuide();
};

/* 常用联系人快捷入口：不用搜索也能直接点 */
function renderQuick(){
  const top=S.contacts.slice(0,8);
  $('#quickPick').innerHTML = top.length? '<span class="tiny dim">常用：</span>'
    + top.map(c=>`<button class="qp ${c.chat_id===S.chat?'on':''}"
        data-id="${esc(c.chat_id)}" data-name="${esc(c.name)}">
        ${av(c.name,18)}<span>${esc(c.name)}</span>
        <em>${(c.n/10000>=1?(c.n/10000).toFixed(1)+'万':c.n)}</em></button>`).join('')
    : '';
  $$('#quickPick .qp').forEach(el=>el.onclick=()=>
    pickPerson(el.dataset.id, el.dataset.name));
}
$('#q').onfocus=()=>{ $('#plist').classList.add('on'); renderPicker(); };
$('#q').oninput=()=>{ $('#plist').classList.add('on'); renderPicker(); };
document.addEventListener('click',e=>{
  if(!$('#picker').contains(e.target)) $('#plist').classList.remove('on'); });

/* ---------- 标签 ---------- */
$('#tabs').onclick=e=>{
  const b=e.target.closest('button'); if(!b) return;
  S.view=b.dataset.v;
  $$('#tabs button').forEach(x=>x.classList.toggle('on',x===b));
  $$('.view').forEach(v=>v.classList.toggle('on', v.id==='v-'+S.view));
  loadView();
};

function loadView(){
  // 年度报告 / 聊天即消费 / 业务线 都**不依赖选人**，先处理
  // （踩过：原来把 !S.chat 的早退放在最前，导致业务线标签点进去一片空白。）
  if(S.view==='year'){ loadYear(); return; }
  if(S.view==='consume'){ loadConsume(); return; }
  if(S.view==='pipe'){ loadPipeline(); return; }
  // 聊天与关系：没选人时给引导（不报错、不空白）
  if(!S.chat){ showGuide(); return; }
  loadPeople();
}

/* 聊天与关系 = 聊天报告 + 关系分析（合并成一个标签，一起加载） */
function loadPeople(){
  $('#peopleGuide').style.display='none';
  $('#rangeCard').style.display='';
  $('#evSetup').style.display='';
  $('#anaCard').style.display='';
  loadReport();
  loadEvents();
}

function showGuide(){
  $('#peopleGuide').style.display='';
  $('#rangeCard').style.display='none';
  $('#evSetup').style.display='none';
  $('#evCard').style.display='none';
  $('#anaCard').style.display='none';
  $('#rpBody').innerHTML='';
}

/* ══════════ 年度报告（全局，不依赖选人） ══════════ */
let YEAR=null;
async function loadYear(){
  if(YEAR){ renderYear(); return; }
  $('#yrBody').innerHTML='<div class="card"><div class="loading">'+
    '<span class="spin"></span>正在统计你的这一年…</div></div>';
  YEAR=await api('/api/year');
  renderYear();
}

function fmtN(n){
  n=n||0;
  if(n>=1e8) return (n/1e8).toFixed(2)+'亿';
  if(n>=1e4) return (n/1e4).toFixed(1)+'万';
  return n.toLocaleString();
}

function renderYear(){
  const d=YEAR;
  if(!d||!d.total){ $('#yrBody').innerHTML=
    '<div class="card"><div class="empty">还没有数据</div></div>'; return; }
  const days=d.days, span=d.start+' → '+d.end;
  // 最忙的一天
  const bs=d.busiest||{d:'',n:0};
  // 热力图：全部日期
  const maxDay=Math.max(...d.daily.map(x=>x.n),1);
  const heat=d.daily.map(x=>{
    const t=x.n/maxDay;
    const col=t>.75?'#12a150':t>.5?'#3ea06a':t>.25?'#8fc4a4':'#e9ecef';
    return `<i style="background:${col}" title="${x.d} · ${x.n} 条"></i>`;}).join('');
  // 称号
  const titles=(d.titles||[]).map(t=>`<div class="tcard">
      <div class="ic">${t.icon}</div><div class="nm">${esc(t.name)}</div>
      <div class="ds">${esc(t.desc)}</div></div>`).join('');
  // 口头禅
  const mx=(d.catchphrases[0]||{n:1}).n;
  const phrases=(d.catchphrases||[]).map(c=>{
    const cls=c.n>=mx*.6?'big':c.n>=mx*.3?'mid':'';
    return `<span class="${cls}">${esc(c.w)}<small>${c.n} 次</small></span>`;}).join('');
  // 聊最多的人
  const pmax=(d.people[0]||{n:1}).n;
  const rankbars=(d.people||[]).slice(0,8).map((p,i)=>{
    const h=hue(p.name), w=p.n/pmax*100;
    const grad=i===0?'linear-gradient(90deg,#f0c04a,#d99a1e)':
      i===1?'linear-gradient(90deg,#ccd1d9,#9aa1ac)':
      i===2?'linear-gradient(90deg,#d9a878,#b87a4a)':
      `linear-gradient(90deg,hsl(${h} 62% 54%),hsl(${(h+40)%360} 62% 44%))`;
    return `<div class="rb">${av(p.name)}
      <div class="mid"><div class="nm">${esc(p.name)}
        ${p.is_group?'<span class="tag">群</span>':''}</div>
        <div class="tr"><div class="fl" style="width:${Math.max(3,w)}%;background:${grad}"></div></div></div>
      <div class="vl">${fmtN(p.n)} 条</div></div>`;}).join('');
  // 小时分布
  const hmax=Math.max(...d.hours,1);
  const hcols=d.hours.map((n,i)=>`<div class="col">
      <div class="tip">${String(i).padStart(2,'0')}:00 · ${n} 条</div>
      <div class="stack" style="height:${Math.max(2,Math.round(n/hmax*100))}%">
        <div class="${i>=23||i<6?'sg-think':'sg-in'}" style="height:100%"></div></div>
      <div class="lbl">${i%3===0?i:''}</div></div>`).join('');
  // 谁最黏你 / 你最爱找谁
  // ⚠️ 踩过的坑：这两个榜单的字段是 `.n`（不是 people 的 in/out），
  //    一开始照抄成 p.in / p.out，页面就全显示"给你发了 0 条"。
  const clingy=(d.clingy||[]).slice(0,5).map((p,i)=>
    `<div class="item"><div class="body"><div class="t1">${i+1}. ${esc(p.name)}</div>
      <div class="t2">给你发了 ${fmtN(p.n)} 条</div></div></div>`).join('');
  const fav=(d.favorite||[]).slice(0,5).map((p,i)=>
    `<div class="item"><div class="body"><div class="t1">${i+1}. ${esc(p.name)}</div>
      <div class="t2">你发了 ${fmtN(p.n)} 条</div></div></div>`).join('');
  // 类型
  const KIND={text:'文字',sticker:'表情',image:'图片',quote:'引用',voice:'语音',
    video:'视频',app:'链接',location:'位置',card:'名片',system:'系统',unknown:'其他'};
  const kmax=Math.max(...d.kinds.map(k=>k.n),1);
  const kinds=d.kinds.map(k=>`<div class="bar-row"><div class="nm">${esc(KIND[k.k]||k.k)}</div>
    <div class="tr"><div class="fl" style="width:${k.n/kmax*100}%"></div></div>
    <div class="vl">${fmtN(k.n)}</div></div>`).join('');
  // 一句话
  const topName=d.people[0]?d.people[0].name:'';
  const quip = `这一年你发了 <span class="em">${fmtN(d.n_out)}</span> 条消息，
    收到 <span class="em">${fmtN(d.n_in)}</span> 条。
    和你聊得最多的是 <span class="em">${esc(topName)}</span>。`;

  // 年度评语：用数据规则生成（零成本，而且每条都能对上数字）
  const cmts=[];
  const perDay=Math.round(d.total/Math.max(days,1));
  const top3pct=d.people.slice(0,3).reduce((s,p)=>s+p.n,0)/Math.max(d.total,1);
  const c=p=>({...p});
  // 1. 话量
  if(perDay>=300) cmts.push(c({i:'🌊',t:'重度话痨',
    s:`平均每天 ${perDay} 条。别人用微信聊天，你用微信呼吸。`}));
  else if(perDay>=80) cmts.push(c({i:'💬',t:'稳定输出',
    s:`平均每天 ${perDay} 条，不算话痨也不算冷淡，属于正常人。`}));
  else cmts.push(c({i:'🍃',t:'惜字如金',
    s:`平均每天只有 ${perDay} 条。你的话都留给重要的人了。`}));
  // 2. 作息
  if(d.late_pct>=18) cmts.push(c({i:'🌙',t:'夜猫子实锤',
    s:`${d.late_pct}% 的消息在 23 点后或凌晨发出，最活跃时段是 `+
      `${String(d.busiest_hour).padStart(2,'0')}:00。早睡这件事，明年再说。`}));
  else if(d.late_pct<=5) cmts.push(c({i:'🌅',t:'作息健康',
    s:`只有 ${d.late_pct}% 的消息在深夜，你这个作息在年轻人里算稀有物种。`}));
  // 3. 集中度
  if(top3pct>=0.7) cmts.push(c({i:'💎',t:'话都给了一个人',
    s:`前三个人占了 ${Math.round(top3pct*100)}% 的消息，`+
      `其中「${esc(topName)}」一个人就 ${fmtN(d.people[0]?.n||0)} 条。`}));
  else if(d.chats>=60) cmts.push(c({i:'🦋',t:'广撒网',
    s:`跟 ${d.chats} 个会话聊过，前三个只占 ${Math.round(top3pct*100)}%。`+
      `你的社交面比大多数人宽。`}));
  // 4. 连续性
  if(d.streak>=60) cmts.push(c({i:'🔥',t:'雷打不动',
    s:`最长连续 ${d.streak} 天有消息（${esc(d.streak_range[0])} ~ `+
      `${esc(d.streak_range[1])}）。这个习惯比大部分人的健身卡都持久。`}));
  else if(d.streak<=7) cmts.push(c({i:'📮',t:'间歇性社交',
    s:`最长只连续聊了 ${d.streak} 天。想起来才聊，也挺好。`}));
  // 5. 口头禅
  const phr=(d.catchphrases||[])[0];
  if(phr) cmts.push(c({i:'🗣',t:'你的口头禅',
    s:`「${esc(phr.w)}」说了 ${phr.n} 次。如果一年只能用一句话概括你，大概就是它。`}));
  // 6. 最忙的一天
  if(bs.d) cmts.push(c({i:'📈',t:'最疯狂的一天',
    s:`${esc(bs.d)} 你发了 ${fmtN(bs.n)} 条。那天到底发生了什么？`}));
  const comments=cmts.map(x=>`
    <div class="cmt"><span class="ci">${x.i}</span>
      <div><div class="ct">${esc(x.t)}</div>
        <div class="cs">${x.s}</div></div></div>`).join('');

  $('#yrBody').innerHTML=`
  <div class="hero">
    <div class="tagline">YOUR WECHAT YEAR</div>
    <h2>你的这一年</h2>
    <div class="sub">${esc(span)} · 共 ${days} 个有消息的日子</div>
    <div class="huge"><b>${fmtN(d.total)}</b><span>条消息</span></div>
    <div class="herostats">
      <div><div class="k">我发出的</div><div class="v">${fmtN(d.n_out)}</div></div>
      <div><div class="k">收到的</div><div class="v">${fmtN(d.n_in)}</div></div>
      <div><div class="k">聊过的会话</div><div class="v">${d.chats}</div></div>
      <div><div class="k">写了多少字</div><div class="v">${fmtN(d.chars)}</div></div>
      <div><div class="k">最长连续聊</div><div class="v">${d.streak} 天</div></div>
    </div>
  </div>

  <div class="section"><div class="quip">${quip}</div></div>

  ${titles?`<div class="section"><div class="card">
    <div class="card-head"><span>你的称号</span>
      <span class="hint">根据这一年的聊天数据算出来的</span></div>
    <div class="titles">${titles}</div></div></div>`:''}

  <div class="section grid g2">
    <div class="card">
      <div class="card-head"><span>你的口头禅</span>
        <span class="hint">只统计你发的、2~6 字的词</span></div>
      <div class="phrases">${phrases||'<span class="dim">数据不足</span>'}</div>
    </div>
    <div class="card">
      <div class="card-head"><span>作息 · 24 小时</span>
        <span class="hint">紫色=深夜（${d.late_pct}% 的消息在这时段）</span></div>
      <div class="chart" style="height:150px">${hcols}</div>
    </div>
  </div>

  <div class="section grid g2">
    <div class="card">
      <div class="card-head"><span>聊得最多的人</span>
        <span class="hint">只算单聊</span></div>
      <div class="rankbars">${rankbars}</div>
    </div>
    <div class="card">
      <div class="card-head"><span>这一年你都在忙什么</span></div>
      <div class="list">
        <div class="item"><div class="body">
          <div class="t1">最忙的一天</div>
          <div class="t2">${esc(bs.d)} · ${fmtN(bs.n)} 条</div></div></div>
        <div class="item"><div class="body">
          <div class="t1">最活跃的时段</div>
          <div class="t2">${d.busiest_hour!=null?String(d.busiest_hour).padStart(2,'0')+':00':'—'}</div></div></div>
        <div class="item"><div class="body">
          <div class="t1">最长连续聊天天数</div>
          <div class="t2">${d.streak} 天（${esc(d.streak_range[0])} ~ ${esc(d.streak_range[1])}）</div></div></div>
        ${d.longest?`<div class="item"><div class="body">
          <div class="t1">写过最长的一条 · ${d.longest.len} 字</div>
          <div class="t2">在「${esc(d.longest.chat)}」里</div>
          <div class="quote">${esc((d.longest.text||'').slice(0,110))}</div>
        </div></div>`:''}
      </div>
    </div>
  </div>

  <div class="section grid g2">
    <div class="card">
      <div class="card-head"><span>谁最黏你</span>
        <span class="hint">对方发给你的单聊消息</span></div>
      <div class="list">${clingy||'<div class="empty tiny">无</div>'}</div>
    </div>
    <div class="card">
      <div class="card-head"><span>你最爱找谁</span>
        <span class="hint">你发出的单聊消息</span></div>
      <div class="list">${fav||'<div class="empty tiny">无</div>'}</div>
    </div>
  </div>

  <div class="section"><div class="card">
    <div class="card-head"><span>这一年的聊天热力</span>
      <span class="hint">每一格是一天，越亮聊得越多</span></div>
    <div class="heat">${heat}</div>
    <div class="legend">
      <span>少</span>
      <i style="background:#e9ecef"></i><i style="background:#8fc4a4"></i>
      <i style="background:#3ea06a"></i><i style="background:#12a150"></i>
      <span>多</span>
      <span style="margin-left:auto">共 ${days} 天有消息</span>
    </div>
  </div></div>

  <div class="section"><div class="card">
    <div class="card-head"><span>📝 给你的年度评语</span>
      <span class="hint">根据数据算出来的，不是鸡汤</span></div>
    <div class="comments">${comments}</div>
  </div></div>

  <div class="section"><div class="card">
    <div class="card-head"><span>消息类型</span></div>
    <div class="bars">${kinds}</div>
  </div></div>`;
}

/* ══════════ 聊天与关系 = 报告 + 关系分析（合并） ══════════ */
$('#periods').onclick=e=>{
  const b=e.target.closest('button'); if(!b) return;
  S.period=b.dataset.p;
  $$('#periods button').forEach(x=>x.classList.toggle('on',x===b));
  loadReport();
  // 时间范围要**同步到下面的事件列表**（用户要求）
  loadEvents();
};
$('#rpExport').onclick=()=>{ if(S.chat)
  location.href='/api/export/report?chat='+encodeURIComponent(S.chat)+'&period='+S.period; };

// 「全部时间」→ days=0；其余由后端返回的 days_n 决定
function periodDays(){
  const map={recent3:3,recent7:7,recent30:30,week:8,month:31,quarter:93,year:365};
  return map[S.period]!==undefined? map[S.period] : 0;
}

async function loadReport(){
  if(!S.chat) return;
  $('#rpBody').innerHTML='<div class="card"><div class="loading">'+
    '<span class="spin"></span>生成中…</div></div>';
  const d=await api('/api/report?chat='+encodeURIComponent(S.chat)+'&period='+S.period);
  if($('#rpTitle')) $('#rpTitle').textContent=(d.name||S.name)+' · '+(d.title||'');
  if(d.error){ $('#rpBody').innerHTML='<div class="card"><div class="empty">'+esc(d.error)+'</div></div>'; return; }
  if(!d.total){ $('#rpBody').innerHTML='<div class="card"><div class="empty">'+
    '<span class="big-ico">🗓</span>「'+esc(d.title)+'」你们没有消息</div></div>';
    renderComment(''); return; }
  const maxN=Math.max(...d.series.map(x=>x.n),1);
  // 天数太多时按 N 天聚合，否则柱子会细到看不见、日期标签也会挤成一片
  // （用户反馈"消息量走势是一条线"就是这个原因）
  // ⚠️ 阈值要按 **series 实际长度** 判断，不能用 period 猜 ——
  //    'all' 看起来跨度 243 天，但 series 是完整日历天（含大量 0 值），
  //    一开始只在 series.length>48 时才聚合，结果"全部"仍然画了 67 根。
  const MAXCOL=44;
  let series=d.series;
  if(series.length>MAXCOL){
    const AGG=Math.ceil(series.length/MAXCOL);
    const out=[];
    for(let i=0;i<series.length;i+=AGG){
      const g=series.slice(i,i+AGG);
      out.push({label:g[0].label, d:g[0].d, n:g.reduce((s,x)=>s+x.n,0),
                note:g.length>1?`${g[0].label}~${g[g.length-1].label}`:g[0].label});
    }
    series=out;
  }
  const amax=Math.max(...series.map(x=>x.n),1);
  // 柱子少才显示每个标签，多了只显示首尾，避免挤成一团
  const showLbl=series.length<=20;
  const cols=series.map((x,i)=>`
    <div class="col"><div class="tip">${esc(x.note||x.d)} · ${x.n} 条</div>
      <div class="stack" style="height:${Math.max(3,Math.round(x.n/amax*100))}%">
        <div class="sg-in" style="height:100%"></div></div>
      <div class="lbl">${showLbl?esc(x.label):((i===0||i===series.length-1||i%8===0)?esc(x.label):'')}</div></div>`).join('');
  const hmax=Math.max(...d.hours,1);
  const hcols=d.hours.map((n,i)=>`
    <div class="col"><div class="tip">${String(i).padStart(2,'0')}:00 · ${n} 条</div>
      <div class="stack" style="height:${Math.max(2,Math.round(n/hmax*100))}%">
        <div class="${i>=23||i<6?'sg-think':'sg-in'}" style="height:100%"></div></div>
      <div class="lbl">${i%3===0?String(i).padStart(2,'0'):''}</div></div>`).join('');
  const kmax=Math.max(...d.kinds.map(k=>k.n),1);
  const KIND={text:'文字',sticker:'表情',image:'图片',quote:'引用',voice:'语音',
    video:'视频',app:'链接',location:'位置',card:'名片',system:'系统',unknown:'其他'};
  const hl=d.highlights||{}; const hls=[];
  if(hl.busiest) hls.push(['最忙的一天',hl.busiest.day+'（'+hl.busiest.n+' 条）']);
  if(hl.peak_hour!=null) hls.push(['最活跃时段',String(hl.peak_hour).padStart(2,'0')+':00']);
  if(hl.late_n) hls.push(['深夜消息',hl.late_n+' 条（'+hl.late_pct+'%）']);
  if(hl.longest) hls.push(['最长的一条',hl.longest.len+' 字']);
  hls.push(['谁开启话题','我 '+hl.init_mine+' 次 / 对方 '+hl.init_their+' 次']);
  $('#rpBody').innerHTML=`
  <div class="kpis">
    <div class="kpi"><div class="k">消息总数</div><div class="v">${d.total.toLocaleString()}</div>
      <div class="s">${d.days} 个活跃天 · ${esc(d.title)}</div></div>
    <div class="kpi"><div class="k">我发出的</div><div class="v green">${d.n_out.toLocaleString()}</div>
      <div class="s">占 ${Math.round(d.n_out/d.total*100)}%</div></div>
    <div class="kpi"><div class="k">对方发的</div><div class="v blue">${d.n_in.toLocaleString()}</div>
      <div class="s">日均 ${Math.round(d.total/Math.max(d.days,1))} 条</div></div>
    <div class="kpi"><div class="k">字数</div><div class="v amber">${d.chars.toLocaleString()}</div></div>
    <div class="kpi"><div class="k">Token</div><div class="v violet">${d.tok.toLocaleString()}</div></div>
  </div>
  <div class="section"><div class="card">
    <div class="card-head"><span>消息量走势</span><span class="hint">悬停看每天</span></div>
    <div class="chart">${cols}</div></div></div>
  <div class="section grid g2">
    <div class="card"><div class="card-head"><span>24 小时作息</span>
      <span class="hint">紫色=深夜</span></div>
      <div class="chart" style="height:120px">${hcols}</div></div>
    <div class="card"><div class="card-head"><span>高光时刻</span></div>
      <div class="list">${hls.map(x=>`<div class="item"><div class="body">
        <div class="t1">${esc(x[0])}</div><div class="t2">${esc(x[1])}</div>
        </div></div>`).join('')}</div></div>
  </div>
  <div class="section"><div class="card">
    <div class="card-head"><span>消息类型</span></div>
    <div class="bars">${d.kinds.map(k=>`<div class="bar-row">
      <div class="nm">${esc(KIND[k.k]||k.k)}</div>
      <div class="tr"><div class="fl" style="width:${k.n/kmax*100}%"></div></div>
      <div class="vl">${k.n.toLocaleString()}</div></div>`).join('')}</div>
  </div></div>

  <div class="section"><div class="card">
    <div class="card-head"><span>🤖 AI 点评</span>
      <span class="hint" id="cmtMeta">根据这段时间的数字写，会引用具体数据</span></div>
    <div class="ai-comment" id="cmtBox">
      <span class="spin"></span>正在写点评…</div>
  </div></div>`;
  loadComment();
}

/* AI 点评：每次生成报告都重新写一段（用户要求"把信息发给 ai 做出回应"） */
async function loadComment(){
  if(!S.chat) return;
  const box=$('#cmtBox'); if(!box) return;
  box.innerHTML='<span class="spin"></span>正在写点评…';
  const r=await post('/api/comment',{chat:S.chat, period:S.period});
  if(r.error){ box.innerHTML='<span class="dim tiny">点评生成失败：'+esc(r.error)+'</span>'; return; }
  box.innerHTML=esc(r.comment||'').replace(/\n+/g,'<br>');
  if(r.cost && $('#cmtMeta')) $('#cmtMeta').textContent=
    'AI 点评 · '+esc(S.name)+' · '+esc(S.period)+' · ¥'+r.cost.toFixed(5);
}

/* ══════════ 关系分析（事件驱动） ══════════ */
const TONE_META={serious:['🔴','认真'],playful:['🟢','玩闹'],
                 mixed:['🟡','半真半假'],neutral:['⚪','平实']};

$('#evEstimate').onclick=async()=>{
  if(!S.chat) return alert('先选一个人');
  const r=await api('/api/estimate?chat='+encodeURIComponent(S.chat)
    +'&days='+periodDays());
  if(r.error) return alert(r.error);
  $('#evHint').textContent=`约 ${r.chunks_total} 块 · 预计 ${r.est_seconds}s · ¥${r.est_cost}`;
};
$('#evExtract').onclick=async()=>{
  if(!S.chat) return alert('先选一个人');
  if(!confirm('抽取会清空这个人的旧事件并重新跑。已取消各类上限，会比较久（几分钟），继续？')) return;
  const r=await post('/api/events/extract',{chat:S.chat, days:periodDays()});
  if(r.error) return alert(r.error);
  $('#evProg').classList.add('on'); $('#evHint').textContent='抽取中…';
  pollState(()=>{ $('#evProg').classList.remove('on');
    $('#evHint').textContent='抽取完成'; loadEvents(); });
};
$('#evMore').onclick=()=>{ S.shown+=20; renderEvents(); };
$('#evSort').onchange=()=>loadEvents();
$('#evFilters').onclick=e=>{
  const c=e.target.closest('.fchip'); if(!c) return;
  const k=c.dataset.k||'', t=c.dataset.t||'';
  if(c.dataset.t!==undefined && t) { S.toneFilter = S.toneFilter===t?'':t; }
  else { S.filter = k; }
  S.shown=20; renderEvents();
};
$('#anaRun').onclick=async()=>{
  if(!S.chat) return alert('先选一个人');
  if(!S.events.length) return alert('先抽取事件');
  $('#anaProg').classList.add('on');
  $('#anaBox').innerHTML='<div class="loading"><span class="spin"></span>'+
    '正在基于事件做专业分析…（内容较长，约 20~40 秒）</div>';
  const r=await post('/api/analyze',{chat:S.chat,
    kinds:S.filter?[S.filter]:null, days:periodDays()});
  $('#anaProg').classList.remove('on');
  if(r.error){ $('#anaBox').innerHTML='<div style="color:var(--rose)">'+esc(r.error)+'</div>'; return; }
  $('#anaHint').textContent=`用了 ${r.used}/${r.n_events} 个事件 · ¥${r.cost.toFixed(4)}`;
  $('#anaBox').innerHTML=md2html(r.analysis);
};

async function loadEvents(){
  if(!S.chat) return;
  // 时间范围跟随上面的周期选择（用户要求）
  const days=periodDays();
  const sort=$('#evSort')? $('#evSort').value : 'time';
  const r=await api('/api/events?chat='+encodeURIComponent(S.chat)
    +'&days='+days+'&sort='+sort);
  S.events=r.events||[]; S.kinds=r.kinds||[]; S.tones=r.tones||{};
  S.filter=''; S.toneFilter=''; S.shown=20;
  const scope = days? `最近 ${days} 天` : '全部时间';
  $('#evStat').textContent = r.total
    ? `${r.total} 个事件（${scope}）` : `这段时间（${scope}）还没有事件`;
  $('#evCard').style.display = r.total? 'block':'none';
  $('#anaBox').innerHTML='';
  if(r.total) renderEvents();
  else $('#evList').innerHTML='';
}

function renderEvents(){
  const counts={};
  S.events.forEach(e=>counts[e.kind]=(counts[e.kind]||0)+1);
  // 类型筛选
  let chips = `<div class="fchip ${S.filter===''&&!S.toneFilter?'on':''}" data-k="">全部
      <span class="n">${S.events.length}</span></div>`
    + S.kinds.filter(k=>counts[k.kind]).map(k=>`
      <div class="fchip ${S.filter===k.kind?'on':''}" data-k="${k.kind}">
        ${k.icon} ${esc(k.label)} <span class="n">${counts[k.kind]}</span></div>`).join('');
  // 情绪性质筛选（区分真吵架/假吵架）
  const tk=Object.keys(S.tones).filter(t=>S.tones[t]>0);
  if(tk.length>1){
    chips += `<span class="tiny dim" style="margin:0 4px">｜情绪</span>`
      + tk.map(t=>{const m=TONE_META[t]||['⚪',t];
        return `<div class="fchip ${S.toneFilter===t?'on':''}" data-t="${t}">
          ${m[0]} ${esc(m[1])} <span class="n">${S.tones[t]}</span></div>`;}).join('');
  }
  $('#evFilters').innerHTML = chips;
  let list=S.events.filter(e=>!S.filter||e.kind===S.filter);
  if(S.toneFilter) list=list.filter(e=>e.tone===S.toneFilter);
  $('#evList').innerHTML = list.slice(0,S.shown).map(e=>{
    const tm=TONE_META[e.tone]||['⚪','平实'];
    const toneCls = e.tone==='serious'?'rose':e.tone==='playful'?'green':
                    e.tone==='mixed'?'amber':'';
    return `
    <div class="ecard" data-cid="${esc(S.chat)}" data-lid="${e.local_id}" data-id="${e.id}">
      <div class="h"><span class="ic">${e.icon}</span>
        <span class="lb">${esc(e.label)}</span>
        <span class="lb ${toneCls}" title="情绪性质：${esc(tm[1])}">${tm[0]} ${esc(tm[1])}</span>
        <span class="tiny dim">${e.actor?esc(e.actor)+' · ':''}</span>
        <span class="dt">${esc(e.day)}</span></div>
      <div class="sm">${esc(e.summary)}</div>
      ${e.detail?`<div class="tiny dim" style="margin-top:4px">${esc(e.detail)}</div>`:''}
      ${e.quote?`<div class="qt">${esc(e.quote)}</div>`:''}
    </div>`;}).join('');
  $('#evMore').style.display = list.length>S.shown? 'inline-block':'none';
  $$('#evList .ecard').forEach(el=>el.onclick=()=>toggleCtx(el));
}

async function toggleCtx(el){
  if(el.classList.contains('open')){ el.classList.remove('open');
    const c=el.querySelector('.ctx'); if(c) c.remove(); return; }
  el.classList.add('open');
  const box=document.createElement('div');
  box.className='ctx';
  box.innerHTML='<div class="tiny dim">读取上下文…</div>';
  el.appendChild(box);
  setTimeout(function(){ var rc=el.getBoundingClientRect();
    if(rc.top<80||rc.bottom>window.innerHeight)
      el.scrollIntoView({block:'center',behavior:'smooth'}); },120);
  const r=await api('/api/context?chat='+encodeURIComponent(el.dataset.cid)
    +'&local_id='+el.dataset.lid);
  if(r.error){ box.innerHTML='<div class="tiny" style="color:var(--rose)">'+esc(r.error)+'</div>'; return; }
  box.innerHTML='<div class="tiny dim" style="margin:2px 0 6px">前后各 7 条原文（蓝底=依据那条）</div>'
    + (r.rows||[]).map(x=>`<div class="r ${x.local_id==el.dataset.lid?'hit':''}">
        <span class="w">${esc(x.time)}</span>
        <span class="w">${x.direction==='out'?'我':'对方'}</span>
        <span class="t">${esc(x.text)}</span></div>`).join('');
}

/* ══════════ 聊天即消费（完整版子应用，嵌入式） ══════════
   不重写那套仪表盘 —— 它有自己的 5 个视图（总览/聊天/计费/模型市场/实验室）、
   额度管理、API Key 管理、模型市场、按人塞 Key。重写一遍风险大且会丢功能。
   所以作为**子应用完整嵌入**：功能一个不少，主页面导航仍然统一。
   ⚠️ 子应用是独立进程（默认 8899），start.ps1 会一起拉起来。
*/
const CONSUME_URL = 'http://127.0.0.1:8899/';
let CS_LOADED = false;

function loadConsume(){
  if(CS_LOADED) return;
  CS_LOADED = true;
  $('#csBody').innerHTML = `
  <div class="card" style="padding:0;overflow:hidden">
    <div class="card-head" style="padding:14px 18px 0">
      <span>完整版仪表盘</span>
      <span class="rowflex">
        <span class="tiny dim">总览 · 聊天 · 计费 · 模型市场 · 实验室</span>
        <a class="btn ghost small" href="${CONSUME_URL}" target="_blank"
           style="text-decoration:none">新窗口打开 ↗</a>
        <button class="btn ghost small" id="csReload">重新加载</button>
      </span>
    </div>
    <div style="padding:12px 14px 14px">
      <iframe id="csFrame" src="${CONSUME_URL}" title="聊天即消费"
        style="width:100%;height:calc(100vh - 210px);min-height:640px;
               border:1px solid var(--line);border-radius:12px;background:#ffffff"
        referrerpolicy="no-referrer"></iframe>
      <div class="tiny dim" style="margin-top:9px">
        这是原来那个完整仪表盘（含额度管理、API Key、模型市场、按人塞 Key），
        作为子页面嵌入。若显示空白说明子服务没起来 —— 运行 <code>.\\start.ps1</code> 会一起拉起。
      </div>
    </div>
  </div>`;
  const b = document.getElementById('csReload');
  if(b) b.onclick = () => { const f=document.getElementById('csFrame');
    if(f) f.src = CONSUME_URL + '?_=' + Date.now(); };
}

/* ---------- 业务线 ---------- */
async function loadPipeline(){
  const r=await api('/api/templates');
  S.tpls=r.templates||[];
  if(!S.pipelineTpl && S.tpls.length) S.pipelineTpl=S.tpls[0].id;
  $('#tplGrid').innerHTML=S.tpls.map(t=>`
    <div class="tpl-card ${t.id===S.pipelineTpl?'on':''}" data-id="${t.id}">
      <div class="tpl-top"><div class="tpl-ico">${t.icon||'⚙️'}</div>
        <div><div class="tpl-nm">${esc(t.name)}</div>
          <div class="tiny dim">${esc(t.hint||'')}</div></div></div>
      <div class="tpl-desc">${esc(t.desc||'')}</div>
      <div class="tpl-fields">${(t.fields||[]).map(f=>
        `<span class="tag">${esc(f.label)}</span>`).join('')}</div></div>`).join('');
  $$('#tplGrid .tpl-card').forEach(c=>c.onclick=()=>{
    S.pipelineTpl=c.dataset.id; loadPipeline(); loadPipeResults(); });
  loadPipeResults();
}

/* ══════════ 业务线：范围多选下拉 ══════════
   用户要求：范围可以是好友也可以是群聊，且能多选。
   这里列**全部会话**（含群聊），带「好友/群聊」标记，支持筛选和批量勾选。
*/
let PP_ALL=[], PP_SEL=new Set();
async function loadScopeList(){
  if(PP_ALL.length) return renderScopeList();
  const r=await api('/api/contacts?groups=1');   // 含群聊
  PP_ALL=r.contacts||[];
  renderScopeList();
  updateScopeN();
}
function renderScopeList(){
  const q=($('#ppScopeSearch').value||'').trim().toLowerCase();
  const list=PP_ALL.filter(c=>!q||c.name.toLowerCase().includes(q));
  $('#ppScopeList').innerHTML = list.length? list.slice(0,400).map(c=>`
    <label class="msel-item">
      <input type="checkbox" data-id="${esc(c.chat_id)}"
        ${PP_SEL.has(c.chat_id)?'checked':''}>
      <span class="nm">${esc(c.name)}</span>
      <span class="k">${c.is_group?'群聊':'好友'}</span>
      <span class="mt">${c.n>=10000?(c.n/10000).toFixed(1)+'万':c.n}</span>
    </label>`).join('') : '<div class="msel-item dim">没有匹配的会话</div>';
  $$('#ppScopeList input[type=checkbox]').forEach(el=>el.onchange=()=>{
    if(el.checked) PP_SEL.add(el.dataset.id); else PP_SEL.delete(el.dataset.id);
    updateScopeN();
  });
}
function updateScopeN(){
  const n=PP_SEL.size;
  $('#ppScopeN').textContent = n? `已选 ${n} 个` : '全部';
}
$('#ppScopeBtn').onclick=e=>{
  e.stopPropagation();
  $('#ppScopePop').classList.toggle('on');
  loadScopeList();
};
document.addEventListener('click',e=>{
  if(!$('#ppScope').contains(e.target)) $('#ppScopePop').classList.remove('on');
});
$('#ppScopeSearch').oninput=()=>renderScopeList();
$('#ppScopePop').addEventListener('click',e=>{
  const b=e.target.closest('button[data-pick]'); if(!b) return;
  const mode=b.dataset.pick;
  const visible=PP_ALL.filter(c=>{
    const q=($('#ppScopeSearch').value||'').trim().toLowerCase();
    return !q||c.name.toLowerCase().includes(q);
  });
  if(mode==='all') visible.forEach(c=>PP_SEL.add(c.chat_id));
  if(mode==='none') PP_SEL.clear();
  if(mode==='dm') visible.filter(c=>!c.is_group).forEach(c=>PP_SEL.add(c.chat_id));
  if(mode==='grp') visible.filter(c=>c.is_group).forEach(c=>PP_SEL.add(c.chat_id));
  renderScopeList(); updateScopeN();
});

$('#ppRun').onclick=async()=>{
  if(!S.pipelineTpl) return alert('先选业务线');
  const r=await post('/api/pipeline/run',{tpl:S.pipelineTpl,
    chats:[...PP_SEL], days:+$('#ppDays').value||180});
  if(r.error) return alert(r.error);
  $('#ppProg').classList.add('on'); $('#ppHint').textContent='抽取中…';
  pollState(()=>{ $('#ppProg').classList.remove('on');
    $('#ppHint').textContent='完成'; loadPipeResults(); });
};
$('#ppRefresh').onclick=loadPipeResults;
$('#ppSearch').oninput=()=>renderPipeTable();
$('#ppFuzzy').onchange=()=>renderPipeTable();
$('#ppCsv').onclick=()=>{ if(S.pipelineTpl)
  location.href='/api/csv?tpl='+encodeURIComponent(S.pipelineTpl); };

let PP_ROWS=[], PP_TPL={fields:[],entity_label:'条目'};
let PP_F={status:'', kinds:new Set(), urgency:new Set(), tone:new Set()};
const PP_STATUS=[
  {k:'',        label:'全部'},
  {k:'new',     label:'未处理'},
  {k:'follow',  label:'跟进中'},
  {k:'done',    label:'已处理'},
  {k:'ignore',  label:'已忽略'},
  {k:'starred', label:'⭐ 星标'},
];

async function loadPipeResults(){
  if(!S.pipelineTpl) return;
  PP_TPL=S.tpls.find(x=>x.id===S.pipelineTpl)||{fields:[],entity_label:'条目'};
  const r=await api('/api/pipeline?tpl='+encodeURIComponent(S.pipelineTpl));
  PP_ROWS=(r.rows||[]).slice();
  PP_F={status:'', kinds:new Set(), urgency:new Set(), tone:new Set()};
  renderStatusBar(); renderFacets(); renderPipeTable();
}

/* 状态计数条：点一下即过滤（用户模拟："不知道多少条待处理"） */
function renderStatusBar(){
  const cnt={total:PP_ROWS.length, new:0, follow:0, done:0, ignore:0, starred:0};
  PP_ROWS.forEach(g=>{
    const st=g.status||'new'; cnt[st]=(cnt[st]||0)+1;
    if(g.starred) cnt.starred++;
  });
  $('#ppStatusBar').innerHTML = PP_STATUS.map(s=>{
    const n = s.k==='' ? cnt.total : (s.k==='starred'? cnt.starred : (cnt[s.k]||0));
    return `<button class="stchip ${PP_F.status===s.k?'on':''}" data-st="${s.k}">
      ${esc(s.label)} <b>${n}</b></button>`;
  }).join('');
  $$('#ppStatusBar .stchip').forEach(b=>b.onclick=()=>{
    PP_F.status = PP_F.status===b.dataset.st? '' : b.dataset.st;
    renderStatusBar(); renderPipeTable();
  });
}

/* 多条件筛选 chips：枚举模板里 enum 类型字段的所有值 */
function renderFacets(){
  const facets=[];
  PP_TPL.fields.forEach(f=>{
    if(f.type==='enum' && f.values && f.values.length){
      const vals={};
      PP_ROWS.forEach(g=>{ const v=g.fields[f.key]; if(v) vals[v]=(vals[v]||0)+1; });
      const used=Object.keys(vals);
      if(used.length>1) facets.push({key:f.key, label:f.label, vals, used});
    }
  });
  // 情绪性质（冲突类才有意义，但通用显示）
  const tones={}; PP_ROWS.forEach(g=>{ if(g.meta_tone) tones[g.meta_tone]=(tones[g.meta_tone]||0)+1; });
  $('#ppFacets').innerHTML = facets.map(f=>`
    <div class="facet">
      <span class="facet-l">${esc(f.label)}</span>
      <span class="facet-v">
        ${f.used.map(v=>`<button class="fchip2 ${PP_F.kinds.has(f.key+'='+v)?'on':''}"
           data-k="${esc(f.key)}" data-v="${esc(v)}">${esc(v)}
           <em>${f.vals[v]}</em></button>`).join('')}
      </span>
    </div>`).join('') || '<span class="tiny dim">这个模板没有可筛选的枚举字段</span>';
  $$('#ppFacets .fchip2').forEach(b=>b.onclick=()=>{
    const key=b.dataset.k+'='+b.dataset.v;
    if(PP_F.kinds.has(key)) PP_F.kinds.delete(key); else PP_F.kinds.add(key);
    renderFacets(); renderPipeTable();
  });
}

/* 语义扩展提示：告诉用户"我还匹配了这些词"（可解释，不是黑箱） */
let PP_SYN_TIMER=null;
async function updateSynHint(){
  const q=($('#ppSearch').value||'').trim();
  const box=$('#ppSynHint');
  if(!q || !$('#ppSem').checked){ box.innerHTML=''; return; }
  const r=await api('/api/pipeline/expand?q='+encodeURIComponent(q));
  if(!r.groups || !r.groups.length){
    box.innerHTML=`<span class="dim">「${esc(q)}」没有同义词，按原词匹配</span>`;
    return;
  }
  box.innerHTML = r.groups.map(g=>
    `「${esc(g.word)}」还匹配：<span class="syn">${
      g.expanded.filter(w=>w!==g.word).slice(0,10).map(esc).join(' / ')}</span>`
  ).join('　');
}

$('#ppRefresh').onclick=loadPipeResults;
$('#ppSearch').oninput=()=>{
  clearTimeout(PP_SYN_TIMER);
  PP_SYN_TIMER=setTimeout(updateSynHint, 350);
  renderPipeTable();
};
$('#ppSem').onchange=()=>{ updateSynHint(); renderPipeTable(); };
$('#ppFuzzy').onchange=()=>renderPipeTable();
$('#ppCsv').onclick=()=>{ if(S.pipelineTpl)
  location.href='/api/csv?tpl='+encodeURIComponent(S.pipelineTpl); };

/* 标记状态 / 星标 */
async function markRow(fp, patch){
  const r=await post('/api/pipeline/label',
    Object.assign({tpl:S.pipelineTpl, fp:fp}, patch));
  if(r.error) return alert(r.error);
  const row=PP_ROWS.find(g=>g.fp===fp);
  if(row){ if(patch.status) row.status=patch.status;
           if(patch.starred!==undefined) row.starred=patch.starred; }
  renderStatusBar(); renderPipeTable();
}

function rowHay(g){
  return [g.entity, ...Object.values(g.fields||{}), (g.chats||[]).join(' ')]
    .join(' ').toLowerCase();
}

function renderPipeTable(){
  const t=PP_TPL;
  if(!PP_ROWS.length){ $('#ppBody').innerHTML=
    '<div class="empty"><span class="big-ico">📭</span>还没有结果 —— '+
    '选好业务线和范围，点「开始抽取」</div>'; return; }

  // ---- 逐层过滤 ----
  let rows=PP_ROWS;
  // 1) 状态
  if(PP_F.status==='starred') rows=rows.filter(g=>g.starred);
  else if(PP_F.status) rows=rows.filter(g=>(g.status||'new')===PP_F.status);
  // 2) 枚举多选（同一字段内 OR，不同字段间 AND）
  if(PP_F.kinds.size){
    const byField={};
    PP_F.kinds.forEach(k=>{
      const [fk,fv]=k.split('=');
      (byField[fk]=byField[fk]||new Set()).add(fv);
    });
    Object.keys(byField).forEach(fk=>{
      rows=rows.filter(g=>byField[fk].has(g.fields[fk]));
    });
  }
  // 3) 文本搜索（多词 AND；语义扩展时每词再 OR 同义词）
  const q=($('#ppSearch').value||'').trim();
  const fuzzy=$('#ppFuzzy').checked;
  const sem=$('#ppSem').checked;
  if(q){
    const words=q.toLowerCase().split(/\s+/).filter(Boolean);
    rows=rows.filter(g=>{
      const hay=rowHay(g);
      return words.every(w=>{
        const terms = (sem && window._PP_SYN && window._PP_SYN[w])
          ? window._PP_SYN[w] : [w];
        const hit = terms.some(t=>fuzzy? hay.includes(t) : new RegExp(
          t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'i').test(hay));
        // 非模糊时：先试整词精确，再退回包含（中文没有词边界，完全精确会漏太多）
        return fuzzy ? hit : (hay.includes(w) || hit);
      });
    });
  }
  // 星标置顶 + 时间倒序
  rows=rows.slice().sort((a,b)=>
    ((b.starred?1:0)-(a.starred?1:0)) ||
    String(b.last_day||'').localeCompare(String(a.last_day||'')));

  const crit=[];
  if(PP_F.status) crit.push('状态='+ (PP_STATUS.find(s=>s.k===PP_F.status)||{}).label);
  PP_F.kinds.forEach(k=>crit.push(k.replace('=','=')));
  if(q) crit.push('搜索「'+q+'」');

  const show=rows.slice(0,300);
  const head=`<div class="tiny dim" style="margin-bottom:9px">
    命中 <b>${rows.length}</b> / 共 ${PP_ROWS.length} 条
    ${crit.length? '· 条件：'+esc(crit.join(' 且 ')) : ''}
    · 星标置顶 · 按时间倒序
    ${rows.length>300?'· 只显示前 300 条':''}</div>`;
  $('#ppCriteria').innerHTML = crit.length
    ? '当前条件：' + esc(crit.join(' 且 ')) +
      ` <button class="btn ghost small" onclick="PP_F={status:'',kinds:new Set(),urgency:new Set(),tone:new Set()};renderStatusBar();renderFacets();renderPipeTable()">清空条件</button>`
    : '';

  $('#ppBody').innerHTML = head + (show.length? `
    <div class="tbl-wrap" style="max-height:640px;overflow:auto">
    <table class="tbl"><thead><tr>
      <th>⭐</th><th>状态</th><th>最近</th><th>${esc(t.entity_label)}</th>
      ${t.fields.map(f=>`<th>${esc(f.label)}</th>`).join('')}
      <th class="num">出处</th><th>来源会话</th></tr></thead><tbody>
    ${show.map(g=>{
      const st=g.status||'new';
      const stLabel={new:'未处理',follow:'跟进中',done:'已处理',ignore:'已忽略'}[st]||st;
      return `<tr class="${st==='done'||st==='ignore'?'ro-dim':''}">
      <td><input type="checkbox" class="pp-star" data-fp="${esc(g.fp)}"
            ${g.starred?'checked':''} title="星标置顶"></td>
      <td><select class="pp-st" data-fp="${esc(g.fp)}">
        ${['new','follow','done','ignore'].map(k=>
          `<option value="${k}" ${st===k?'selected':''}>${
            {new:'未处理',follow:'跟进中',done:'已处理',ignore:'已忽略'}[k]}</option>`).join('')}
      </select></td>
      <td class="dim" style="white-space:nowrap">${esc(g.last_day||'—')}</td>
      <td><b>${esc(g.entity)}</b></td>
      ${t.fields.map(f=>{
        const v=g.fields[f.key];
        return `<td>${v? esc(v) : '<span class="dim tiny">未提到</span>'}</td>`;
      }).join('')}
      <td class="num tiny dim" title="${esc((g.quotes||[]).join(' / '))}">
        ${g.n_sources} 条${(g.quotes&&g.quotes[0])?`<br><span class="qt-mini">${
          esc(g.quotes[0].slice(0,14))}…</span>`:''}</td>
      <td class="dim" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;
        white-space:nowrap" title="${esc((g.chats||[]).join('、'))}">${
        esc((g.chats||[]).slice(0,2).join('、'))}${
        (g.chats||[]).length>2?' +'+(g.chats.length-2):''}</td>
    </tr>`;}).join('')}</tbody></table></div>`
    : '<div class="empty"><span class="big-ico">🔍</span>没有匹配的记录'+
      '<div class="tiny dim" style="margin-top:8px">试试放宽条件，或关掉部分筛选</div></div>');

  // 绑定交互
  $$('#ppBody .pp-star').forEach(el=>el.onchange=()=>
    markRow(el.dataset.fp,{starred:el.checked}));
  $$('#ppBody .pp-st').forEach(el=>el.onchange=()=>
    markRow(el.dataset.fp,{status:el.value}));
}

// 同步一份同义词表到前端（供过滤时使用）
(async function(){
  try{
    const probes=['兼职','家教','二手','球局','急','通知','机会','求购','租房','免费'];
    window._PP_SYN={};
    for(const w of probes){
      const r=await api('/api/pipeline/expand?q='+encodeURIComponent(w));
      if(r.terms && r.terms.length>1) window._PP_SYN[w]=r.terms.map(x=>x.toLowerCase());
    }
  }catch(e){ window._PP_SYN={}; }
})();

/* ---------- 进度轮询 ---------- */
async function pollState(done){
  for(let i=0;i<600;i++){
    await new Promise(r=>setTimeout(r,1500));
    const st=await api('/api/state');
    if(st.msg) { const h=$('#evHint'); if($('#evProg').classList.contains('on')&&h) h.textContent=st.msg;
                 const p=$('#ppHint'); if($('#ppProg').classList.contains('on')&&p) p.textContent=st.msg; }
    if(!st.running){ if(st.error) alert('失败：'+st.error); done&&done(); return; }
  }
}

/* ---------- markdown ---------- */
function md2html(md){
  if(!md) return '';
  let s=esc(md);
  s=s.replace(/^#### (.*)$/gm,'<h4>$1</h4>').replace(/^### (.*)$/gm,'<h3>$1</h3>')
     .replace(/^## (.*)$/gm,'<h3>$1</h3>').replace(/^# (.*)$/gm,'<h3>$1</h3>');
  s=s.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
  s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
  s=s.replace(/\[(\d{2}-\d{2}[^\]]{0,16})\]/g,'<span class="ref">[$1]</span>');
  s=s.replace(/^\s*[-*•]\s+(.*)$/gm,'<li>$1</li>');
  s=s.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g,'<ul>$1</ul>');
  s=s.split(/\n{2,}/).map(p=>/^<(h3|h4|ul|li)/.test(p.trim())?p:'<p>'+p+'</p>').join('\n');
  return s.replace(/<p>\s*<\/p>/g,'');
}

/* ---------- 启动 ---------- */
(async()=>{
  const r=await api('/api/contacts');
  S.contacts=r.contacts||[];
  renderPicker();
  renderQuick();       // 常用联系人快捷入口
  // 显式按当前标签加载，而不是依赖 S.view 的初始值对得上。
  // （踩过：S.view 初值写的是别的标签，但 HTML 默认显示年度报告，
  //   于是 loadView 走进"还没选人 → 不加载"的分支，年度报告区永远空白。）
  const cur=document.querySelector('#tabs button.on');
  S.view = cur ? cur.dataset.v : 'year';
  loadView();
})();
</script>
</body></html>"""
