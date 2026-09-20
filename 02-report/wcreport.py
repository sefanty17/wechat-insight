# -*- coding: utf-8 -*-
"""
wcreport.py — 聊天报告生成器（年 / 季 / 月 / 周）

产出一个**自包含的单文件 HTML**：样式内联、数据内联、无外部依赖，
双击就能看，也能直接发给别人（数据是聊天统计，发之前自己看一眼）。

设计要点：
  · 视觉沿用 00-core/wc-design.css 的设计语言（深色 + 微信绿/电光蓝）
  · 四种周期**一次全算好内联进去**，前端切换只切 display，不重新渲染 —— 切换是瞬时的
  · 遵守两条性能红线：不用 background-attachment:fixed、不用 backdrop-filter
  · 图表用 CSS flex + 高度百分比，不用 Canvas/JS 绘图 —— DOM 少、滚动流畅
  · ⚠️ 丑话在前：报告里会出现联系人名字和原话片段，**发给别人前先自己过一遍**

用法：
  python wcreport.py --period week            # 本周
  python wcreport.py --period month --out r.html
  python wcreport.py --period year --no-agenda
"""
from __future__ import annotations

import html
import json
import os
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "00-core")
for p in (CORE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from wcstore import Store  # noqa: E402
from wcagenda import Agenda  # noqa: E402

CSS_FILE = os.path.join(CORE, "wc-design.css")

WEEKDAY = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
KIND_LABEL = {
    "text": "文字", "sticker": "表情", "image": "图片", "quote": "引用",
    "voice": "语音", "video": "视频", "app": "链接", "location": "位置",
    "card": "名片", "system": "系统", "unknown": "其他",
}


# ==========================================================================
# 周期
# ==========================================================================

def period_range(period: str, ref: datetime | None = None):
    """返回 (开始ts, 结束ts, 标题)。period: year|quarter|month|week"""
    now = ref or datetime.now()
    if period == "week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        title = f"{start:%Y年%m月%d日} 那周"
        sub = f"{start:%m/%d} - {(end - timedelta(days=1)):%m/%d}"
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
        title = f"{start:%Y年%m月}"
        sub = f"{start:%m/%d} - {(end - timedelta(days=1)):%m/%d}"
    elif period == "quarter":
        q = (now.month - 1) // 3
        start = now.replace(month=q * 3 + 1, day=1, hour=0, minute=0,
                            second=0, microsecond=0)
        em = q * 3 + 4
        end = (start.replace(month=em) if em <= 12
               else start.replace(year=start.year + 1, month=1))
        title = f"{start.year}年 第{q+1}季度"
        sub = f"{start:%m/%d} - {(end - timedelta(days=1)):%m/%d}"
    else:  # year
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
        title = f"{start.year}年"
        sub = f"{start:%m/%d} - {(end - timedelta(days=1)):%m/%d}"
    return int(start.timestamp()), int(end.timestamp()), title, sub


# ==========================================================================
# 数据
# ==========================================================================

def build_data(s: Store, t0: int, t1: int, with_agenda: bool = True) -> dict:
    con = s.con
    q = "from messages where t>=? and t<?"
    a = (t0, t1)

    total = con.execute(f"select count(*) {q}", a).fetchone()[0]
    if not total:
        return {"total": 0}

    n_in = con.execute(f"select count(*) {q} and direction='in'", a).fetchone()[0]
    n_out = total - n_in
    n_days = con.execute(
        f"select count(distinct day) {q}", a).fetchone()[0] or 1
    n_chats = con.execute(f"select count(distinct chat_id) {q}", a).fetchone()[0]
    tok = con.execute(f"select sum(tok) {q}", a).fetchone()[0] or 0
    chars = con.execute(f"select sum(length(text)) {q}", a).fetchone()[0] or 0

    # 按天（图上用完整天数序列，没消息的天也要有，否则图会失真）
    day_rows = {r[0]: (r[1], r[2]) for r in con.execute(
        f"select day, count(*), sum(case when direction='out' then 1 else 0 end) {q} "
        f"group by day", a)}
    days = []
    d = datetime.fromtimestamp(t0)
    end = datetime.fromtimestamp(t1)
    while d < end:
        k = d.strftime("%Y-%m-%d")
        n, o = day_rows.get(k, (0, 0))
        days.append({"d": k, "label": f"{d.month}/{d.day}", "wd": d.weekday(),
                     "n": n, "out": o, "in": n - o})
        d += timedelta(days=1)

    # 按小时
    hours = [0] * 24
    for r in con.execute(f"select strftime('%H', t, 'unixepoch', 'localtime') h, "
                         f"count(*) {q} group by h", a):
        try:
            hours[int(r[0])] = r[1]
        except (TypeError, ValueError):
            pass

    # 按类型
    kinds = [{"k": r[0], "n": r[1], "label": KIND_LABEL.get(r[0], r[0])}
             for r in con.execute(f"select kind, count(*) {q} group by kind "
                                  f"order by 2 desc", a)]

    # 按人 Top
    people = []
    for r in con.execute(
            f"select m.chat_id, c.name, c.is_group, count(*) n, "
            f"sum(case when m.direction='out' then 1 else 0 end) o, "
            f"sum(case when m.direction='in' then 1 else 0 end) i "
            f"from messages m left join chats c on c.chat_id=m.chat_id "
            f"where m.t>=? and m.t<? group by m.chat_id order by n desc limit 14", a):
        people.append({"chat_id": r[0], "name": r[1] or r[0][:16],
                       "is_group": bool(r[2]), "n": r[3], "out": r[4], "in": r[5]})

    # 主动性：我开启话题的天数 vs 对方
    # 近似算法：把每天每个会话的第一条消息算作"发起"
    init_mine = init_their = 0
    try:
        for r in con.execute(
                "select chat_id, day, direction from messages where t>=? and t<? "
                "and kind='text' order by chat_id, day, local_id", a):
            pass
        rows = con.execute(
            "select m.chat_id, m.day, m.direction from messages m "
            "join (select chat_id, day, min(local_id) lid from messages "
            "      where t>=? and t<? group by chat_id, day) f "
            "  on f.chat_id=m.chat_id and f.day=m.day and f.lid=m.local_id", a)
        for r in rows:
            if r[2] == "out":
                init_mine += 1
            else:
                init_their += 1
    except Exception:
        pass

    data = {
        "total": total, "n_in": n_in, "n_out": n_out, "n_days": n_days,
        "n_chats": n_chats, "tok": round(tok), "chars": chars,
        "avg_per_day": round(total / n_days, 1),
        "days": days, "hours": hours, "kinds": kinds, "people": people,
        "init_mine": init_mine, "init_their": init_their,
    }

    # 高光
    hl = {}
    act = [x for x in days if x["n"]]
    if act:
        busiest = max(act, key=lambda x: x["n"])
        hl["busiest_day"] = {"day": busiest["d"], "n": busiest["n"]}
    peak_h = max(range(24), key=lambda h: hours[h]) if any(hours) else None
    hl["peak_hour"] = peak_h
    late = sum(hours[23:24] + hours[0:6])
    hl["late_night"] = late
    hl["late_ratio"] = round(late / total * 100, 1) if total else 0
    # 最长的一条消息
    r = con.execute(f"select chat_id, t, direction, text {q} and kind='text' "
                    f"order by length(text) desc limit 1", a).fetchone()
    if r:
        hl["longest"] = {"t": r[1], "dir": r[2], "len": len(r[3] or ""),
                         "text": (r[3] or "")[:160]}
    # 沉默最久的人（期间只在最近出现过一次也算）
    data["highlights"] = hl

    # 约定（可选）
    if with_agenda:
        try:
            ag = Agenda(store=s, verbose=False)
            data["agenda"] = ag.list_open(limit=300)
            # 只留这段时间内的
            data["agenda"] = [x for x in data["agenda"] if t0 <= x["t"] < t1]
        except Exception as e:
            data["agenda"] = []
            data["agenda_error"] = f"{type(e).__name__}: {e}"
    return data


# ==========================================================================
# 渲染
# ==========================================================================

def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def hue(s: str) -> int:
    h = 0
    for c in (s or "?"):
        h = (h * 31 + ord(c)) % 360
    return h


def avatar(name: str, size: int = 28) -> str:
    h = hue(name)
    return (f'<span class="av" style="width:{size}px;height:{size}px;'
            f'background:linear-gradient(135deg,hsl({h} 62% 52%),'
            f'hsl({(h+42)%360} 62% 42%))">{esc((name or "?")[:1])}</span>')


def render_chart(days: list, max_cols: int = 70) -> str:
    """按天柱状图。天数太多时按周聚合，避免柱子过密。"""
    if not days:
        return '<div class="empty"><span class="big-ico">📊</span>这段时间没有消息</div>'
    if len(days) > max_cols:
        buckets = []
        for i in range(0, len(days), 7):
            chunk = days[i:i + 7]
            buckets.append({
                "label": chunk[0]["label"],
                "n": sum(c["n"] for c in chunk),
                "out": sum(c["out"] for c in chunk),
                "in": sum(c["in"] for c in chunk),
                "tip": f"{chunk[0]['label']} ~ {chunk[-1]['label']}",
            })
        days = buckets
    mx = max((d["n"] for d in days), default=1) or 1
    cols = []
    for d in days:
        h = max(2, round(d["n"] / mx * 100))
        pi = (d["in"] / d["n"] * 100) if d["n"] else 0
        po = 100 - pi
        cols.append(
            f'<div class="col">'
            f'<div class="tip">{esc(d.get("tip") or d["label"])}'
            f'<br>{d["n"]} 条 · 收{d["in"]} 发{d["out"]}</div>'
            f'<div class="stack" style="height:{h}%">'
            f'<div class="sg-out" style="height:{po:.1f}%"></div>'
            f'<div class="sg-in" style="height:{pi:.1f}%"></div>'
            f'</div><div class="lbl">{esc(d["label"])}</div></div>')
    return f'<div class="chart">{"".join(cols)}</div>'


def render_hours(hours: list) -> str:
    mx = max(hours) or 1
    cols = []
    for h, n in enumerate(hours):
        ht = max(2, round(n / mx * 100))
        cls = "sg-think" if (h >= 23 or h < 6) else "sg-in"
        cols.append(f'<div class="col"><div class="tip">{h:02d}:00 · {n} 条</div>'
                    f'<div class="stack" style="height:{ht}%">'
                    f'<div class="{cls}" style="height:100%"></div></div>'
                    f'<div class="lbl">{h if h % 3 == 0 else ""}</div></div>')
    return f'<div class="chart" style="height:130px">{"".join(cols)}</div>'


def render_period(key: str, period: str, data: dict, sub: str) -> str:
    if not data.get("total"):
        return (f'<section class="period" data-p="{key}">'
                f'<div class="card"><div class="empty">'
                f'<span class="big-ico">🗓</span>{esc(sub)} 没有任何消息记录</div>'
                f'</div></section>')
    d = data
    init_tot = (d["init_mine"] + d["init_their"]) or 1
    init_pct = round(d["init_mine"] / init_tot * 100)

    # KPI
    kpis = [
        ("消息总数", f'{d["total"]:,}', f'日均 {d["avg_per_day"]} 条', ""),
        ("我发出的", f'{d["n_out"]:,}', f'占 {round(d["n_out"]/d["total"]*100)}%', "green"),
        ("收到的", f'{d["n_in"]:,}', f'{d["n_chats"]} 个会话', "blue"),
        ("活跃天数", f'{d["n_days"]}', "有消息的天", ""),
        ("聊到的人", f'{len(d["people"])}+', "会话数", "violet"),
        ("Token 量", f'{d["tok"]:,}', f'{d["chars"]:,} 字', "amber"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="k">{esc(k)}</div>'
        f'<div class="v {c}">{esc(v)}</div><div class="s">{esc(s)}</div></div>'
        for k, v, s, c in kpis)

    # 排行
    mx = max((p["n"] for p in d["people"]), default=1)
    rank = []
    for i, p in enumerate(d["people"], 1):
        tag = '<span class="tag">群</span>' if p["is_group"] else ""
        rank.append(
            f'<div class="row">{avatar(p["name"])}'
            f'<div class="mid"><div class="nm">{esc(p["name"])} {tag}</div>'
            f'<div class="sb">收 {p["in"]} · 发 {p["out"]}</div></div>'
            f'<div class="val">{p["n"]:,}</div></div>')
    rank_html = f'<div class="rank">{"".join(rank)}</div>' if rank else \
        '<div class="empty">没有数据</div>'

    # 类型
    kmx = max((k["n"] for k in d["kinds"]), default=1)
    kinds_html = "".join(
        f'<div class="bar-row"><div class="nm">{esc(k["label"])}</div>'
        f'<div class="tr"><div class="fl" style="width:{k["n"]/kmx*100:.1f}%"></div></div>'
        f'<div class="vl">{k["n"]:,}</div></div>' for k in d["kinds"])

    # 高光
    hl = d.get("highlights") or {}
    ht = []
    if hl.get("busiest_day"):
        b = hl["busiest_day"]
        ht.append(f'<div class="item"><div class="body">'
                  f'<div class="t1">最忙的一天 · <b>{esc(b["day"])}</b></div>'
                  f'<div class="t2">{b["n"]} 条消息</div></div></div>')
    if hl.get("peak_hour") is not None:
        ht.append(f'<div class="item"><div class="body">'
                  f'<div class="t1">最活跃时段 · <b>{hl["peak_hour"]:02d}:00</b></div>'
                  f'<div class="t2">这个点你最常说话</div></div></div>')
    if hl.get("late_night"):
        ht.append(f'<div class="item"><div class="body">'
                  f'<div class="t1">深夜消息 · <b>{hl["late_night"]:,} 条</b></div>'
                  f'<div class="t2">23:00-06:00 占 {hl.get("late_ratio", 0)}%</div></div></div>')
    if hl.get("longest"):
        L = hl["longest"]
        ht.append(f'<div class="item"><div class="body">'
                  f'<div class="t1">最长的一条 · <b>{L["len"]} 字</b>'
                  f'（{"我发的" if L["dir"]=="out" else "收到的"}）</div>'
                  f'<div class="quote">{esc(L["text"][:120])}</div></div></div>')
    ht.append(f'<div class="item"><div class="body">'
              f'<div class="t1">主动开启话题</div>'
              f'<div class="t2">我 {d["init_mine"]} 次 · 对方 {d["init_their"]} 次'
              f'（我占 {init_pct}%）</div></div>'
              f'<div class="val" style="font-size:13px">{init_pct}%</div></div>')
    highlights_html = f'<div class="list">{"".join(ht)}</div>'

    # 约定挖掘
    agenda_html = ""
    if d.get("agenda") is not None:
        ag = d["agenda"]
        if not ag:
            agenda_html = ('<div class="empty"><span class="big-ico">✅</span>'
                           '这段时间没有待兑现的约定</div>')
        else:
            KIND = {"promise": ("我承诺", "green"), "task": ("对方要求我", "amber"),
                    "plan": ("约定", "blue"), "request": ("我请求对方", "violet"),
                    "their_promise": ("对方承诺", "ghost"),
                    "their_task": ("我要求对方", "ghost"),
                    "their_plan": ("对方约定", "ghost"),
                    "their_request": ("对方请求我", "ghost")}
            items = []
            for x in ag[:60]:
                lab, cls = KIND.get(x["kind"], (x["kind"], ""))
                od = (' <span class="tag rose">已过期</span>'
                      if x.get("overdue") else "")
                when = f' · {esc(x["when_text"])}' if x.get("when_text") else ""
                items.append(
                    f'<div class="item"><div class="body">'
                    f'<div class="t1">{esc(x["what"])} '
                    f'<span class="tag {cls}">{esc(lab)}</span>{od}</div>'
                    f'<div class="t2">{esc(x["chat"])}{when}</div>'
                    f'<div class="quote"><span class="src">{esc(x["day"])}</span>'
                    f'{esc((x.get("quote") or "")[:100])}</div>'
                    f'</div></div>')
            agenda_html = f'<div class="list">{"".join(items)}</div>'

    agenda_section = ""
    if agenda_html:
        n_ag = len(d.get("agenda") or [])
        agenda_section = (
            f'<div class="section"><div class="card">'
            f'<div class="card-head"><span>约定与承诺 · 待兑现</span>'
            f'<span class="hint">{n_ag} 条 · '
            f'从聊天里自动挖出来的，可能有个别误判</span></div>'
            f'{agenda_html}</div></div>')

    return f'''
<section class="period" data-p="{key}">
  <div class="kpis">{kpi_html}</div>
  <div class="section">
    <div class="card">
      <div class="card-head"><span>消息量走势</span>
        <span class="hint">绿=我发的 · 蓝=收到的</span></div>
      {render_chart(d["days"])}
    </div>
  </div>
  <div class="section grid g2">
    <div class="card">
      <div class="card-head"><span>聊得最多的人</span></div>
      {rank_html}
    </div>
    <div class="card">
      <div class="card-head"><span>作息 · 24 小时</span>
        <span class="hint">紫色=深夜</span></div>
      {render_hours(d["hours"])}
    </div>
  </div>
  <div class="section grid g2">
    <div class="card">
      <div class="card-head"><span>消息类型</span></div>
      <div class="bars">{kinds_html}</div>
    </div>
    <div class="card">
      <div class="card-head"><span>高光时刻</span></div>
      {highlights_html}
    </div>
  </div>
  {agenda_section}
</section>'''


def generate(period: str = "month", out: str = "", periods=("year", "quarter", "month", "week"),
             with_agenda: bool = True, store: Store = None) -> str:
    s = store or Store(verbose=False)
    periods = [p for p in periods if p]
    if period not in periods:
        periods = [period] + list(periods)

    blocks, tabs, subs = [], [], {}
    for i, p in enumerate(periods):
        t0, t1, title, sub = period_range(p)
        subs[p] = (title, sub)
        try:
            data = build_data(s, t0, t1, with_agenda=with_agenda)
        except Exception as e:
            data = {"total": 0, "error": f"{type(e).__name__}: {e}"}
        blocks.append(render_period(p, p, data, sub))
        label = {"year": "年度", "quarter": "季度", "month": "月度", "week": "本周"}[p]
        tabs.append(f'<button data-go="{p}" class="{"on" if p == period else ""}">'
                    f'{label}</button>')

    css = ""
    if os.path.exists(CSS_FILE):
        with open(CSS_FILE, encoding="utf-8") as f:
            css = f.read()
    ttl, first_sub = subs.get(period, ("报告", ""))
    now = time.strftime("%Y-%m-%d %H:%M")
    total_all = s.con.execute("select count(*) from messages").fetchone()[0]

    page = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>聊天报告 · {esc(ttl)}</title>
<style>
{css}
.period{{display:none}}
.period.on{{display:block}}
.top .tabs{{margin-left:auto}}
.report-foot{{margin-top:26px;text-align:center;font-size:11.5px;color:var(--txt-3)}}
</style></head>
<body>
<div class="bg-glow" aria-hidden="true"></div>
<div class="top">
  <div class="brand"><div class="logo">📊</div>
    <div><h1>聊天报告 · {esc(ttl)}</h1>
      <div class="sub">{esc(first_sub)} · 生成于 {esc(now)}</div></div></div>
  <div class="tabs" id="tabs">{"".join(tabs)}</div>
</div>
<div class="page">
{"".join(blocks)}
  <div class="report-foot">
    数据来自本机微信记录（共 {total_all:,} 条）· 生成于 {esc(now)}<br>
    报告含联系人名字与原话片段，<b>分享前请自行确认内容</b>
  </div>
</div>
<script>
// 切换周期：只切 display，数据已全部内联，切换是瞬时的（不重新渲染）
(function(){{
  var tabs = document.getElementById('tabs');
  var secs = document.querySelectorAll('.period');
  function go(p){{
    for (var i=0;i<secs.length;i++) secs[i].classList.toggle('on', secs[i].dataset.p===p);
    var bs = tabs.querySelectorAll('button');
    for (var j=0;j<bs.length;j++) bs[j].classList.toggle('on', bs[j].dataset.go===p);
    var m = document.querySelector('.period.on');
    if (m) {{
      var t = m.querySelector('.t1'); // 用标题区更新顶栏，不必重排整页
      var f = m.querySelector('.kpi .v');
      if (f) document.title = '聊天报告 · ' + f.textContent + ' 条消息';
    }}
    history.replaceState(null,'','#'+p);
  }}
  tabs.addEventListener('click', function(e){{
    var b = e.target.closest('button'); if(!b) return; go(b.dataset.go);
  }});
  var h = location.hash.replace('#','');
  if (h) go(h); else go('{period}');
  // 数字键 1-4 快速切换
  document.addEventListener('keydown', function(e){{
    var idx = parseInt(e.key,10)-1;
    var bs = tabs.querySelectorAll('button');
    if (idx>=0 && idx<bs.length) go(bs[idx].dataset.go);
  }});
}})();
</script>
</body></html>'''

    if not out:
        out = os.path.join(HERE, f"report-{period}-{time.strftime('%Y%m%d')}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="聊天报告生成器")
    ap.add_argument("--period", default="month",
                    choices=["year", "quarter", "month", "week"])
    ap.add_argument("--out", default="")
    ap.add_argument("--no-agenda", action="store_true", help="不显示约定挖掘（快）")
    a = ap.parse_args()
    p = generate(a.period, a.out, with_agenda=not a.no_agenda)
    size = os.path.getsize(p) / 1024
    print(f"[+] 报告已生成：{p}  ({size:.0f} KB)")
    print(f"    用浏览器打开即可，四种周期（年/季/月/周）都能切换")
