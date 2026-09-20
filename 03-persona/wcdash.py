# -*- coding: utf-8 -*-
"""
wcdash.py — 03-persona 的关系仪表盘（生成单文件 HTML）

三块内容：
  1. **指标层**：把方法论里的每个指标画成可视化的数字/图表（数据层，最有价值）
  2. **人设层**：LLM 生成的人设与建议（markdown 渲染）
  3. **趋势层**：12 周走势（这是最有信息量的一张图）

设计遵守 00-core/wc-design.css 的设计语言与两条性能红线。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "00-core")
for p in (CORE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from wcstore import Store  # noqa: E402
from wcmetrics import RelationMetrics, candidate_chats  # noqa: E402
from wcpersona import Persona  # noqa: E402

CSS_FILE = os.path.join(CORE, "wc-design.css")


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def md_to_html(md: str) -> str:
    """极简 markdown 渲染（标题/粗体/列表/引用/代码）。

    不引第三方库：只需要支持 LLM 输出的这几种语法就够了。
    """
    if not md:
        return ""
    out = []
    in_ul = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append("")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            lvl = min(len(m.group(1)) + 1, 5)
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            continue
        if re.match(r"^\s*[-*•]\s+", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + inline(re.sub(r"^\s*[-*•]\s+", "", line)) + "</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if line.startswith(">"):
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
            continue
        if re.match(r"^\s*\d+[.、)]\s+", line):
            out.append("<p class='oli'>" + inline(line) + "</p>")
            continue
        if set(line.strip()) <= set("-—=") and len(line.strip()) >= 3:
            out.append("<hr>")
            continue
        out.append(f"<p>{inline(line)}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def inline(s: str) -> str:
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # 引用格式 [时间 说话人] 高亮
    s = re.sub(r"(\[\d{2}-\d{2}[^\]]{0,20}\])", r'<span class="ref">\1</span>', s)
    return s


# ---------------- 组件 ----------------

def gauge(label: str, value, unit: str, note: str, tone: str = "") -> str:
    return (f'<div class="kpi"><div class="k">{esc(label)}</div>'
            f'<div class="v {tone}">{esc(value)}<span class="u"> {esc(unit)}</span></div>'
            f'<div class="s">{esc(note)}</div></div>')


def trend_chart(trends: list) -> str:
    """12 周趋势：消息量柱 + 正面占比折线（用柱高度近似）。"""
    if not trends:
        return ""
    mx = max((t["n"] for t in trends), default=1) or 1
    cols = []
    for t in trends:
        h = max(2, round(t["n"] / mx * 100)) if t["n"] else 2
        zero = t["n"] == 0
        cls = "sg-think" if zero else "sg-in"
        tip = (f'{t["week"]}：{t["n"]} 条'
               + (f' · 我发 {t["out"]}' if t["n"] else "（无消息）")
               + (f' · 正面 {t["pos_ratio"]}%' if t["n"] else ""))
        cols.append(
            f'<div class="col"><div class="tip">{esc(tip)}</div>'
            f'<div class="stack" style="height:{h}%">'
            f'<div class="{cls}" style="height:100%"></div></div>'
            f'<div class="lbl">{esc(t["week"])}</div></div>')
    return f'<div class="chart">{"".join(cols)}</div>'


def horsemen_block(fh: dict) -> str:
    rows = [
        ("批评", fh["criticism"], "针对人格的概括指责", "改成温柔开场：具体行为+我的感受+我需要什么"),
        ("蔑视", fh["contempt"], "居高临下、嘲讽", "建立欣赏文化：每天具体地表达一次欣赏"),
        ("防御", fh["defensiveness"], "反击或推卸", "先承担 5% 的责任"),
        ("冷战", {"rate": fh["stonewalling"]["rate"],
                  "n": fh["stonewalling"]["brush_off"] + fh["stonewalling"]["long_gaps"],
                  "eg": []}, "敷衍、退出互动", "情绪过载时暂停 20 分钟，但要说清「我待会回来」"),
    ]
    out = []
    for name, d, desc, antidote in rows:
        rate = d.get("rate", 0)
        tone = "green" if rate < 1 else ("amber" if rate < 3 else "rose")
        eg = "".join(f'<div class="quote">{esc(x)}</div>' for x in (d.get("eg") or [])[:3])
        out.append(
            f'<div class="item"><div class="body">'
            f'<div class="t1">{esc(name)} <span class="tag {tone}">{rate}%</span> '
            f'<span class="dim tiny">{esc(desc)} · {d.get("n", 0)} 条</span></div>'
            f'{eg}'
            f'<div class="t2" style="margin-top:6px">💡 {esc(antidote)}</div>'
            f'</div></div>')
    return f'<div class="list">{"".join(out)}</div>'


def bids_block(bi: dict) -> str:
    r = bi["turn_rate"]
    tone = "green" if r >= 60 else ("amber" if r >= 30 else "rose")
    eg = "".join(f'<div class="quote">{esc(x)}</div>' for x in bi["missed_examples"][:4])
    return (f'<div class="big"><span class="n">{r}%</span>'
            f'<span class="u">邀约回应率</span></div>'
            f'<div class="tiny dim" style="margin:6px 0 10px">'
            f'{bi["turned_towards"]} / {bi["bids"]} 次邀约被实质回应 · '
            f'<span class="tag {tone}">'
            f'{"健康" if r >= 60 else "有提升空间" if r >= 30 else "偏低"}</span></div>'
            f'<div class="t2 dim">方法论：这是关系质量最灵敏的单一指标，'
            f'长期低于 30% 是明确风险信号</div>'
            f'{"<div class=\"t2\" style=\"margin-top:8px\">错过的邀约：</div>" + eg if eg else ""}')


def latency_block(lat: dict) -> str:
    m, t = lat.get("mine", {}), lat.get("theirs", {})
    ms, ts_ = lat.get("mine_split", {}), lat.get("theirs_split", {})
    return (f'<div class="grid g2" style="gap:10px">'
            f'<div class="kpi"><div class="k">我的回复中位</div>'
            f'<div class="v blue">{m.get("median_min", "—")}<span class="u"> 分钟</span></div>'
            f'<div class="s">≤30 分钟占 {ms.get("≤30分钟", 0)}%</div></div>'
            f'<div class="kpi"><div class="k">对方的回复中位</div>'
            f'<div class="v green">{t.get("median_min", "—")}<span class="u"> 分钟</span></div>'
            f'<div class="s">≤30 分钟占 {ts_.get("≤30分钟", 0)}%</div></div>'
            f'</div>'
            f'<div class="t2 dim" style="margin-top:10px">⚠️ {esc(lat.get("caveat", ""))}</div>')


def attachment_block(at: dict) -> str:
    a, av = at["anxiety_signal"], at["avoidance_signal"]
    eg = "".join(f'<div class="quote">{esc(x)}</div>' for x in a["eg"][:3])
    direction = ("亲密话题更长 → 无回避倾向"
                 if av["ratio"] >= 1 else
                 "亲密话题更短 → 可能有回避倾向" if av["ratio"] < 0.7 else
                 "两者接近 → 信号不明显")
    return (f'<div class="item"><div class="body">'
            f'<div class="t1">焦虑信号 <span class="tag amber">{a["rate"]}%</span>'
            f'<span class="dim tiny"> · {a["n"]} 条</span></div>'
            f'<div class="t2">追问、确认类表达</div>{eg}</div></div>'
            f'<div class="item"><div class="body">'
            f'<div class="t1">回避信号 <span class="tag blue">{av["ratio"]}</span>'
            f'<span class="dim tiny"> · 亲密话题 {av["deep_avg_len"]} 字 / '
            f'普通 {av["normal_avg_len"]} 字</span></div>'
            f'<div class="t2">{esc(direction)}</div>'
            f'<div class="quote">{esc(av["note"])}</div></div></div>'
            f'<div class="t2 dim" style="margin-top:8px">⚠️ {esc(at.get("caveat", ""))}</div>')


def moments_block(moments: list) -> str:
    if not moments:
        return ('<div class="empty"><span class="big-ico">🌙</span>'
                '没有读到这个人的朋友圈<br>'
                '<span class="tiny">（只能读本机缓存过的动态，不是全量历史）</span></div>')
    items = []
    for x in moments[:30]:
        d = time.strftime("%Y-%m-%d", time.localtime(x["t"])) if x["t"] else ""
        items.append(f'<div class="item"><div class="body"><div class="t1">{esc(x["text"])}</div>'
                     f'<div class="t2">{esc(d)}</div></div></div>')
    return f'<div class="list">{"".join(items)}</div>'


# ---------------- 主渲染 ----------------

def build(chat_id: str, days: int = 0, with_persona: bool = True,
          with_moments: bool = True, out: str = "", verbose: bool = True) -> str:
    s = Store(verbose=False)
    m = RelationMetrics(s)
    nm = s.con.execute("select name,is_group from chats where chat_id=?",
                       (chat_id,)).fetchone()
    name = nm[0] if nm else chat_id
    is_group = bool(nm[1]) if nm else False

    data = m.all(chat_id, days=days)
    b = data["basics"]
    mr = data["magic_ratio"]
    fh = data["four_horsemen"]
    bi = data["bids"]
    lat = data["latency"]
    at = data["attachment"]
    dt = data["deep_topic"]
    trends = data["trends"]

    persona_md, pmeta = "", {}
    moments = []
    if with_persona:
        p = Persona(s, verbose=verbose)
        if verbose:
            print("[*] 生成人设（调用 LLM）…")
        r = p.analyze(chat_id, days=days, with_moments=with_moments)
        persona_md = r["persona"]
        moments = r["moments"]
        pmeta = {"seconds": r["seconds"], "cost": r["cost"]["cost"],
                 "model": r["model"]}
    elif with_moments:
        p = Persona(s, verbose=False)
        moments = p.moments(chat_id)

    # KPI
    kpis = "".join([
        gauge("消息总量", f'{b["total"]:,}', "条", f'{b["days"]} 天 · 日均 {round(b["total"]/max(b["days"],1))} 条'),
        gauge("我发出的", f'{b["out"]:,}', "条", f'占 {round(b["out"]/max(b["total"],1)*100)}%', "green"),
        gauge("对方发的", f'{b["in"]:,}', "条", f'平均 {b["avg_len_in"]} 字', "blue"),
        gauge("主动性比值", b["init_ratio"], "我/对方", f'我 {b["init_mine"]} 天 · 对方 {b["init_their"]} 天'),
        gauge("正面/负面", mr["ratio"], ": 1", mr["verdict"],
              "green" if mr["ratio"] >= 5 else "amber" if mr["ratio"] >= 1 else "rose"),
        gauge("深度话题", f'{dt["ratio"]}%', "", f'{dt["deep"]} / {dt["total"]} 条'),
    ])

    css = open(CSS_FILE, encoding="utf-8").read() if os.path.exists(CSS_FILE) else ""
    now = time.strftime("%Y-%m-%d %H:%M")
    sub = f'{"群聊" if is_group else "单聊"} · 数据窗口 {"全部" if not days else f"最近 {days} 天"}'

    persona_section = ""
    if persona_md:
        persona_section = f'''
<div class="section">
  <div class="card">
    <div class="card-head"><span>人设与相处建议</span>
      <span class="hint">{pmeta.get("model","")} · {pmeta.get("seconds","")}s ·
      ¥{pmeta.get("cost",0):.4f} · 依据 kb/relationship_methodology.md</span></div>
    <div class="persona">{md_to_html(persona_md)}</div>
  </div>
</div>'''

    page = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>关系分析 · {esc(name)}</title>
<style>
{css}
.persona{{font-size:13.5px;line-height:1.75}}
.persona h2{{font-size:16px;margin:20px 0 10px;padding-bottom:6px;
  border-bottom:1px solid var(--line)}}
.persona h3{{font-size:14.5px;margin:18px 0 8px;color:var(--blue-2)}}
.persona h4{{font-size:13.5px;margin:14px 0 6px}}
.persona p{{margin:7px 0}}
.persona ul{{margin:6px 0 6px 18px;padding:0}}
.persona li{{margin:4px 0}}
.persona blockquote{{margin:10px 0;padding:8px 12px;border-left:3px solid var(--blue);
  background:rgba(77,124,254,.07);border-radius:0 8px 8px 0;color:var(--txt-2)}}
.persona code{{font-family:var(--mono);font-size:12px;background:rgba(255,255,255,.08);
  padding:1px 5px;border-radius:5px}}
.persona hr{{border:0;border-top:1px dashed var(--line);margin:16px 0}}
.persona .oli{{margin:5px 0}}
.kpi .u{{font-size:12px;color:var(--txt-3);font-weight:400}}
</style></head>
<body>
<div class="bg-glow" aria-hidden="true"></div>
<div class="top">
  <div class="brand"><div class="logo">💠</div>
    <div><h1>{esc(name)}</h1><div class="sub">{esc(sub)} · 生成于 {esc(now)}</div></div></div>
  <div class="spacer"></div>
  <span class="pill ghost">03-persona</span>
</div>
<div class="page">
  <div class="kpis">{kpis}</div>

  <div class="section">
    <div class="card">
      <div class="card-head"><span>12 周走势</span>
        <span class="hint">柱高=消息量 · 紫色=该周无消息 · 悬停看详情</span></div>
      {trend_chart(trends)}
      <div class="t2 dim" style="margin-top:10px">
        看趋势不看绝对值 —— 单周波动没意义，连续走低才是信号
      </div>
    </div>
  </div>

  <div class="section grid g2">
    <div class="card">
      <div class="card-head"><span>Gottman 四骑士</span>
        <span class="hint">关系破坏性沟通</span></div>
      {horsemen_block(fh)}
    </div>
    <div class="card">
      <div class="card-head"><span>情感邀约回应率</span>
        <span class="hint">turn_rate</span></div>
      {bids_block(bi)}
    </div>
  </div>

  <div class="section grid g2">
    <div class="card">
      <div class="card-head"><span>回复节奏</span></div>
      {latency_block(lat)}
      <div class="card-head" style="margin-top:18px"><span>正面/负面互动</span></div>
      <div class="grid g2" style="gap:10px">
        <div class="kpi"><div class="k">正面</div>
          <div class="v green">{mr["positive"]:,}</div>
          <div class="s">{esc(" · ".join(mr["pos_examples"][:2])[:40])}</div></div>
        <div class="kpi"><div class="k">负面</div>
          <div class="v rose">{mr["negative"]:,}</div>
          <div class="s">{esc(" · ".join(mr["neg_examples"][:2])[:40])}</div></div>
      </div>
      <div class="t2 dim" style="margin-top:8px">⚠️ {esc(mr["caveat"])}</div>
    </div>
    <div class="card">
      <div class="card-head"><span>依恋信号</span>
        <span class="hint">只是信号，不是类型诊断</span></div>
      {attachment_block(at)}
    </div>
  </div>

  <div class="section">
    <div class="card">
      <div class="card-head"><span>这个人的朋友圈</span>
        <span class="hint">只含本机缓存过的动态（你刷到过的），非全量</span></div>
      {moments_block(moments)}
    </div>
  </div>
{persona_section}
  <div class="report-foot">
    数据来自本机微信记录 · 生成于 {esc(now)}<br>
    指标是**统计近似**，不能替代真实沟通；报告含私人内容，分享前请确认
  </div>
</div>
</body></html>'''

    if not out:
        safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", name)[:24]
        out = os.path.join(HERE, f"persona-{safe}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="关系分析仪表盘")
    ap.add_argument("chat", nargs="?", default="")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--no-persona", action="store_true", help="只出指标，不调 LLM")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    s = Store(verbose=False)
    if a.list or not a.chat:
        for c in candidate_chats(s):
            print(f"  {c['name'][:24]:<26}{'群' if c['is_group'] else '单聊':<5}{c['n']:>7} 条")
        raise SystemExit(0)
    hits = s.chat_id_by_name(a.chat)
    if not hits:
        raise SystemExit(f"找不到 {a.chat!r}")
    p = build(hits[0]["chat_id"], days=a.days, with_persona=not a.no_persona,
              out=a.out)
    print(f"[+] 仪表盘已生成：{p}  ({os.path.getsize(p)/1024:.0f} KB)")
