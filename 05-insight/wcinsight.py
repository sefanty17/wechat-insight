# -*- coding: utf-8 -*-
"""
wcinsight.py — 统一洞察页面（聊天报告 / 关系分析 / 业务线）

═══════════════════════════════════════════════════════════════════
  交互设计说明（这是本轮的重点）
═══════════════════════════════════════════════════════════════════
  1. **先选人，再选功能**。顶部一个统一的人员选择器（排掉群聊），
     选了人之后三个功能都作用在这人身上，不用每个功能各选一次。
  2. **关系分析是事件驱动的**：先列出抽好的事件（按重要度排序），
     用户点某个事件 → 就地展开原文上下文 → 核对。
     而不是甩一堆结论让用户信。
  3. **成本透明**：耗时长的操作（抽事件、跑业务线）先给预估，
     用户点确认才跑；跑的时候有进度条。
  4. **大结果不一次渲染**：事件列表分页、报告按需生成，
     避免一次塞几千个 DOM 节点。
  5. **可导出**：报告和分析能导出成单文件 HTML。

  群聊规则：
    · 聊天报告 / 关系分析 → **只看单聊**（群聊没有"我和群"的关系意义）
    · 业务线             → **保留群聊**（那才是它的主场景）
═══════════════════════════════════════════════════════════════════
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
P2 = os.path.join(ROOT, "02-report")
P3 = os.path.join(ROOT, "03-persona")
P4 = os.path.join(ROOT, "04-pipeline")
for p in (CORE, HERE, P2, P3, P4):
    if p not in sys.path:
        sys.path.insert(0, p)

from wcweb import WebApp  # noqa: E402
from wcstore import Store  # noqa: E402

from wcpage import PAGE  # noqa: E402

STORE = Store(verbose=False)
app = WebApp("微信洞察", port=8772)


# ==========================================================================
# 公共
# ==========================================================================

def contacts(include_groups: bool = False) -> list:
    """可选的联系人列表。默认**排除群聊**。"""
    where = "" if include_groups else "where is_group=0"
    rows = STORE.con.execute(
        f"select chat_id,name,is_group,n_total,n_out,n_in,last_t from chats "
        f"{where} order by n_total desc").fetchall()
    out = []
    for cid, nm, grp, n, no, ni, lt in rows:
        if not nm or nm.startswith("wxid_"):
            continue                      # 没名字的（未识别）不列
        if n < 20:
            continue                      # 太少没内容可分析
        out.append({"chat_id": cid, "name": nm, "is_group": bool(grp),
                    "n": n, "n_out": no, "n_in": ni,
                    "last": time.strftime("%Y-%m-%d", time.localtime(lt)) if lt else ""})
    return out


def resolve(chat_id: str):
    r = STORE.con.execute("select name,is_group,n_total from chats where chat_id=?",
                          (chat_id,)).fetchone()
    return (r[0], bool(r[1]), r[2]) if r else (chat_id, False, 0)


def period_range(period: str):
    """周期 → (起, 止, 标题)

    支持：recent3 / recent7 / recent30 / week / month / quarter / year / all
    （用户要求"只输入时间范围"，所以预设得够用，不用他算天数）
    """
    from datetime import datetime, timedelta
    now = datetime.now()
    if period == "all":
        return 0, int(now.timestamp()) + 86400, "全部时间"
    for key, days, label in (("recent3", 3, "最近 3 天"),
                             ("recent7", 7, "最近 7 天"),
                             ("recent30", 30, "最近 30 天")):
        if period == key:
            s = (now - timedelta(days=days)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            return int(s.timestamp()), int(now.timestamp()) + 86400, label
    if period == "year":
        s = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(s.timestamp()), int(s.replace(year=s.year + 1).timestamp()), f"{s.year}年"
    if period == "quarter":
        q = (now.month - 1) // 3
        s = now.replace(month=q * 3 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        em = q * 3 + 4
        e = s.replace(month=em) if em <= 12 else s.replace(year=s.year + 1, month=1)
        return int(s.timestamp()), int(e.timestamp()), f"{s.year}年 第{q+1}季度"
    if period == "week":
        s = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return int(s.timestamp()), int((s + timedelta(days=7)).timestamp()), \
            f"{s:%Y年%m月%d日} 那周"
    s = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    e = (s + timedelta(days=32)).replace(day=1)
    return int(s.timestamp()), int(e.timestamp()), f"{s:%Y年%m月}"


# ==========================================================================
# 1. 聊天报告（单聊）
# ==========================================================================

def report_data(chat_id: str, period: str = "month") -> dict:
    name, is_group, _ = resolve(chat_id)
    t0, t1, title = period_range(period)
    q = "from messages where chat_id=? and t>=? and t<?"
    a = (chat_id, t0, t1)
    total = STORE.con.execute(f"select count(*) {q}", a).fetchone()[0]
    # 把「周期」换算成天数一起返回，前端据此**同步下拉的时间范围到事件列表**
    # （用户要求：改了上面的时间范围，下面的展示范围也要跟着变）
    days_n = 0
    if period not in ("all",) and t1 > t0:
        days_n = max(1, int(round((t1 - t0) / 86400)))
    d = {"chat_id": chat_id, "name": name, "is_group": is_group,
         "period": period, "title": title, "total": total,
         "days_n": days_n, "t0": t0, "t1": t1}
    if not total:
        return d
    n_out = STORE.con.execute(f"select count(*) {q} and direction='out'", a).fetchone()[0]
    n_in = total - n_out
    chars = STORE.con.execute(f"select sum(length(text)) {q}", a).fetchone()[0] or 0
    tok = STORE.con.execute(f"select sum(tok) {q}", a).fetchone()[0] or 0
    days = STORE.con.execute(f"select count(distinct day) {q}", a).fetchone()[0]
    # 天数序列（完整连续，没消息的天也要有，否则图失真）
    day_rows = {r[0]: r[1] for r in STORE.con.execute(
        f"select day,count(*) {q} group by day", a)}
    from datetime import datetime, timedelta
    series, cur = [], datetime.fromtimestamp(t0)
    end = datetime.fromtimestamp(t1)
    if period == "all":
        first = STORE.con.execute("select min(t) from messages where chat_id=?",
                                  (chat_id,)).fetchone()[0]
        if first:
            cur = datetime.fromtimestamp(first)
    while cur < end:
        k = cur.strftime("%Y-%m-%d")
        series.append({"d": k, "label": f"{cur.month}/{cur.day}",
                       "n": day_rows.get(k, 0)})
        cur += timedelta(days=1)
    d.update({"n_out": n_out, "n_in": n_in, "chars": chars, "tok": round(tok),
              "days": days, "series": series})
    # 24 小时
    hours = [0] * 24
    for r in STORE.con.execute(
            f"select strftime('%H',t,'unixepoch','localtime') h,count(*) {q} group by h", a):
        try:
            hours[int(r[0])] = r[1]
        except (TypeError, ValueError):
            pass
    d["hours"] = hours
    # 类型分布
    d["kinds"] = [{"k": r[0], "n": r[1]} for r in STORE.con.execute(
        f"select kind,count(*) {q} group by kind order by 2 desc", a)]
    # 高光
    act = [x for x in series if x["n"]]
    hl = {}
    if act:
        b = max(act, key=lambda x: x["n"])
        hl["busiest"] = {"day": b["d"], "n": b["n"]}
    if any(hours):
        ph = max(range(24), key=lambda h: hours[h])
        hl["peak_hour"] = ph
        late = sum(hours[23:24]) + sum(hours[0:6])
        hl["late_n"] = late
        hl["late_pct"] = round(late / total * 100, 1)
    r = STORE.con.execute(
        f"select t,direction,text {q} and kind='text' order by length(text) desc limit 1",
        a).fetchone()
    if r:
        hl["longest"] = {"t": r[0], "dir": r[1], "len": len(r[2] or ""),
                         "text": (r[2] or "")[:200]}
    # 我发起 vs 对方发起（按天首条）
    init_m = init_t = 0
    for rr in STORE.con.execute(
            "select m.direction from messages m join "
            "(select day,min(local_id) lid from messages where chat_id=? and t>=? and t<? "
            " group by day) f on f.lid=m.local_id", a):
        if rr[0] == "out":
            init_m += 1
        else:
            init_t += 1
    hl["init_mine"], hl["init_their"] = init_m, init_t
    d["highlights"] = hl
    return d


# ==========================================================================
# 2. 关系分析（事件驱动）
# ==========================================================================

def events_list(chat_id: str, kind: str = "", days: int = 0,
                tone: str = "", sort: str = "time") -> dict:
    """列事件 + 统计。

    ⚠️ 踩过的 bug：counts / tones 两个统计查询原来**没带 days 条件**，
    只按 chat_id 分组，于是：
      · rows 正确过滤成 11 条
      · counts 却是全量 1471 → total 恒为 1471
      · 筛选 chips 上的数字也是全量的（点进去数量和标的不一致）
    用户看到"最近 3 天有 1471 个事件"就是这么来的。
    修法：统计也套同一套 where 条件。
    """
    from wcevents import EventStore, kinds_info, tones_info
    es = EventStore()
    rows = es.list(chat_id, kind=kind, days=days, tone=tone,
                   sort=sort, limit=3000)

    cond, args = ["chat_id=?"], [chat_id]
    if days:
        cond.append("t>=?")
        args.append(int(time.time()) - days * 86400)
    w = " and ".join(cond)

    counts, tones = {}, {}
    for r in es.con.execute(
            f"select kind,count(*) from events where {w} group by kind", args):
        counts[r[0]] = r[1]
    for r in es.con.execute(
            f"select tone,count(*) from events where {w} group by tone", args):
        tones[r[0] or "neutral"] = r[1]
    # 注意：已经**取消了各类事件的抽取上限**，所以没有 capped 概念了。
    return {"events": rows, "counts": counts, "tones": tones,
            "kinds": kinds_info(), "tone_defs": tones_info(),
            "days": days, "total": len(rows)}


ANALYZE_SYS = """你在做一份**专业的关系分析**。用户会给你从聊天里抽出的**具体事件**。

═══════════════════════════════════════════════
  第一原则：结论必须落在具体事件上
═══════════════════════════════════════════════
❌ 不要写："你们存在回避倾向"
✅ 要写："3 次涉及关系定义的话题里，2 次是对方岔开的（见 5-12、7-08）"

每条结论后面用 `[日期]` 标注依据。**没有依据的话一句都不要写。**

═══════════════════════════════════════════════
  要分析到什么深度（这是本报告的价值所在）
═══════════════════════════════════════════════
不要只按事件类型平铺。要做**纵向的机制分析**：

1. **冲突的完整生命周期**
   · 谁先起的头（触发方）？触发原因有共性吗？
   · 升级还是很快平息？
   · **谁先低头 / 谁先找台阶**？统计比例——这是关系权力的关键指标
   · 和好用了多久？有没有"假装没吵过"的情况？

2. **回避的具体形态**
   · 什么话题会被岔开？（不是笼统的"敏感话题"，要具体）
   · 用哪种方式回避：换话题 / 敷衍 / 只回表情 / 装没看见？
   · **提问方有没有追问**？追问了几次才放弃？
   · 同一话题被回避了几次——重复回避说明这是稳定模式

3. **修复尝试与回应**
   · 谁在做修复动作（道歉、示好、找台阶）？
   · 对方接不接？
   · 有一方反复尝试却被无视的情况吗？

4. **关心的不对等**
   · 双方各自发起的关心事件数量对比
   · 关心的**具体程度**（是"多喝热水"还是"我给你点了外卖"）
   · 谁生病/低落时被照顾得多

5. **动态变化（有时间序列时一定要做）**
   · 把事件按时间分段（早/中/晚），看**模式有没有变化**
   · 主动性是在增加还是减少？
   · 冲突频率、修复速度在变好还是变差？

6. **重复出现的模式**
   · 同一个矛盾反复出现吗？（列出出现过几次）
   · 有没有"每次都是这个剧本"的迹象

═══════════════════════════════════════════════
  输出结构（严格按这个写，内容要充实）
═══════════════════════════════════════════════
### 一、互动的基本面
用事件说明双方的互动结构：谁主动、谁回应、节奏如何。

### 二、冲突与修复（有冲突事件才写）
按上面「冲突的完整生命周期」逐条分析，带数字和日期。

### 三、回避与回避的话题（有回避事件才写）
按上面「回避的具体形态」分析。指出**被重复回避的具体话题**。

### 四、关心的表达方式
双方各自怎么表达关心，具体程度对比，带例子。

### 五、模式与变化
重复出现的模式 + 时间上的变化趋势。这一段要体现你**读过全部事件**，
能指出跨时间的规律。

### 六、值得注意的信号
2~4 条最值得留意的，每条注明依据和为什么值得注意。

### 七、可以和可以不做的事
· 给 2~4 条**具体到能直接用的沟通建议**（NVC 句式），分「保守/中性/主动」
· 如果有做得好的地方，也要指出来（不要只讲问题）

### 八、这份分析的局限
明确列出材料不足以判断的东西（动机、对方真实想法、线下情况等）。

═══════════════════════════════════════════════
  红线（必须遵守）
═══════════════════════════════════════════════
1. 样本不足（某类事件 < 3 条）就明说"样本太少，看不出模式"，不要硬凑。
2. 只描述行为和模式，**不诊断人格、不预测关系结果**。
3. 不给"该不该表白 / 分手 / 结婚"这类决定。
4. 不编造事件里没有的细节。
5. 语气：专业但直接，像一个懂行的朋友，不要咨询师腔，也不要含糊其辞。
6. 内容和字数要充实——这是"专业分析报告"，不是摘要。"""


def analyze_events(chat_id: str, kinds: list | None = None, days: int = 0,
                   progress=None) -> dict:
    from wcevents import EventStore
    import wcllm
    name, _g, _n = resolve(chat_id)
    es = EventStore()
    evs = es.list(chat_id, kinds=kinds, days=days, limit=200)
    if not evs:
        return {"error": "还没有事件，先点「抽取事件」"}
    # 组装事件文本（控制长度：重要的在前）
    lines = []
    for e in evs[:180]:
        lines.append(f"[{e['day']}] {e['label']}｜{e['actor'] or '?'}｜{e['summary']}"
                     + (f"｜原文：{e['quote'][:40]}" if e.get("quote") else ""))
    by_kind = {}
    for e in evs:
        by_kind.setdefault(e["label"], []).append(e)
    stat = "、".join(f"{k} {len(v)} 条" for k, v in
                     sorted(by_kind.items(), key=lambda kv: -len(kv[1])))
    user = (f"分析对象：{name}\n"
            f"事件总数 {len(evs)} 条（{stat}）\n"
            f"时间范围：{evs[-1]['day']} ~ {evs[0]['day']}\n\n"
            f"【事件列表】（按重要度排序）\n" + "\n".join(lines))

    llm = wcllm.LLM(model=wcllm.MODEL_FAST)
    if progress:
        progress("正在基于事件分析…")
    try:
        text = llm.chat([{"role": "system", "content": ANALYZE_SYS},
                         {"role": "user", "content": user}],
                        model=wcllm.MODEL_FAST, max_tokens=8000, temperature=0.3)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "chat": name, "n_events": len(evs), "stat": stat,
            "analysis": text, "used": min(len(evs), 120),
            "cost": llm.cost()["cost"]}


# ==========================================================================
# 3. 业务线（保留群聊）
# ==========================================================================

def pipeline_templates() -> list:
    import wcpipeline as wcp
    return wcp.load_templates()


def pipeline_results(tpl: str) -> dict:
    import wcpipeline as wcp
    p = wcp.Pipeline(store=STORE, verbose=False)
    return {"rows": p.merge(tpl), "stats": p.stats(tpl),
            "templates": wcp.load_templates()}


# ==========================================================================
# API
# ==========================================================================

@app.get("/api/contacts")
def api_contacts(q):
    inc = (q.get("groups", ["0"])[0] == "1")
    return {"contacts": contacts(include_groups=inc)}


@app.get("/api/report")
def api_report(q):
    cid = (q.get("chat") or [""])[0]
    if not cid:
        return {"error": "缺少 chat"}
    period = (q.get("period") or ["month"])[0]
    return report_data(cid, period)


@app.get("/api/events")
def api_events(q):
    cid = (q.get("chat") or [""])[0]
    if not cid:
        return {"error": "缺少 chat"}
    return events_list(cid, kind=(q.get("kind") or [""])[0],
                       tone=(q.get("tone") or [""])[0],
                       sort=(q.get("sort") or ["time"])[0],
                       days=int((q.get("days") or ["0"])[0]))


@app.get("/api/context")
def api_context(q):
    """取某条消息的前后文（点事件时用）。

    直接查 wcstore，**不要**去 import 01-memory 的 Memory ——
    那样会在项目之间造出依赖，而 01-memory 已经废弃了。
    （踩过一次：目录不在 sys.path 里，直接 ModuleNotFoundError。）
    """
    cid = (q.get("chat") or [""])[0]
    try:
        lid = int((q.get("local_id") or ["0"])[0])
    except ValueError:
        return {"error": "bad local_id"}
    if not cid or not lid:
        return {"error": "缺少参数"}
    rows = STORE.con.execute(
        "select local_id,t,direction,sender_name,kind,text from messages "
        "where chat_id=? and local_id between ? and ? order by local_id",
        (cid, lid - 7, lid + 7)).fetchall()
    return {"chat": resolve(cid)[0],
            "rows": [{"local_id": r[0],
                      "time": time.strftime("%m-%d %H:%M", time.localtime(r[1])),
                      "direction": r[2], "sender": r[3], "kind": r[4],
                      "text": r[5]} for r in rows]}


@app.get("/api/estimate")
def api_estimate(q):
    cid = (q.get("chat") or [""])[0]
    from wcevents import estimate_cost
    if not cid:
        return {"error": "缺少 chat"}
    return estimate_cost(cid, days=int((q.get("days") or ["0"])[0]))


@app.post("/api/events/extract")
def api_extract(body):
    cid = body.get("chat") or ""
    if not cid:
        return {"error": "缺少 chat"}
    days = int(body.get("days") or 0)
    chunks = int(body.get("chunks") or 120)
    if not app.run_bg(_extract_worker, cid, days, chunks):
        return {"error": "已有任务在跑"}
    return {"ok": True}


def _extract_worker(cid, days, chunks):
    from wcevents import extract_chat, EventStore
    es = EventStore()
    es.clear(cid)
    r = extract_chat(cid, days=days, max_chunks=chunks, verbose=False,
                     progress=app.progress)
    app.progress(f"完成：{r['events']} 个事件")
    return r


@app.post("/api/analyze")
def api_analyze(body):
    cid = body.get("chat") or ""
    kinds = body.get("kinds") or None
    days = int(body.get("days") or 0)
    if not cid:
        return {"error": "缺少 chat"}
    return analyze_events(cid, kinds=kinds, days=days, progress=app.progress)


@app.get("/api/templates")
def api_templates(q):
    return {"templates": pipeline_templates()}


# ---------------- AI 评价（每次刷新给一段有针对性的点评）----------------

COMMENT_SYS = """你在给一份**聊天数据报告**写点评。用户会给你一组统计数字。

要求：
1. **必须引用具体数字**，不要空泛（❌"你们聊得很多" ✅"平均每天 8 条"）。
2. 语气：像一个嘴有点毒但心是好的朋友。可以调侃，但不要刻薄、不要评判人品。
3. 发现**值得注意的模式**就点出来，尤其是：
   · 主动性明显不对等（谁开话题多）
   · 深夜聊天占比高 / 作息变化
   · 回复速度的差异
   · 消息量在时间上的变化（突然变多或变少）
4. 如果数据看不出什么有意思的，就**老实说"这段时间挺平淡的"**，不要硬编。
5. 3~5 句话，短句为主，不要用小标题、不要列点。
6. 不要给"该不该表白/分手"这类建议，也不要诊断人格。

只输出点评正文，不要任何前后缀。"""


@app.post("/api/comment")
def api_comment(body):
    """给某个人的某段时间写一段 AI 点评（基于报告数字）。"""
    cid = body.get("chat") or ""
    period = body.get("period") or "month"
    if not cid:
        return {"error": "缺少 chat"}
    d = report_data(cid, period)
    if not d.get("total"):
        return {"comment": "这段时间你们没说话。"}
    hl = d.get("highlights") or {}
    init_m, init_t = hl.get("init_mine", 0), hl.get("init_their", 0)
    tot_i = (init_m + init_t) or 1
    facts = (
        f"对象：{d['name']}\n"
        f"时间范围：{d['title']}（{d['days']} 个有消息的日子）\n"
        f"消息：共 {d['total']} 条，我发 {d['n_out']} 条、对方发 {d['n_in']} 条\n"
        f"日均：{round(d['total'] / max(d['days'], 1))} 条\n"
        f"字数：{d['chars']}\n"
        f"谁开启话题：我 {init_m} 次 / 对方 {init_t} 次（我占 "
        f"{round(init_m / tot_i * 100)}%）\n"
        f"最忙的一天：{hl.get('busiest', {}).get('day', '—')}"
        f"（{hl.get('busiest', {}).get('n', 0)} 条）\n"
        f"最活跃时段：{hl.get('peak_hour', '—')} 点\n"
        f"深夜消息（23点后或凌晨）：{hl.get('late_n', 0)} 条，"
        f"占 {hl.get('late_pct', 0)}%\n"
        f"最长的一条：{hl.get('longest', {}).get('len', 0)} 字"
        f"（{'我发的' if hl.get('longest', {}).get('dir') == 'out' else '对方发的'}）\n"
    )
    try:
        import wcllm
        llm = wcllm.LLM(model=wcllm.MODEL_FAST)
        txt = llm.chat([{"role": "system", "content": COMMENT_SYS},
                        {"role": "user", "content": facts}],
                       model=wcllm.MODEL_FAST, max_tokens=900, temperature=0.7)
        return {"comment": (txt or "").strip(), "cost": llm.cost()["cost"]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@app.get("/api/pipeline")
def api_pipeline(q):
    tpl = (q.get("tpl") or [""])[0]
    if not tpl:
        return {"error": "缺少 tpl"}
    return pipeline_results(tpl)


@app.post("/api/pipeline/label")
def api_pipeline_label(body):
    """标记一条记录的处理状态 / 星标。

    状态放在独立的 record_labels 表里 —— records 是抽取产物，重抽会清空，
    而"我标记过已处理"必须活下来（用户模拟里的原话："每次全量重扫 = 白扫"）。
    """
    import wcpipeline as wcp
    tpl = body.get("tpl") or ""
    fp = body.get("fp") or ""
    if not tpl or not fp:
        return {"error": "缺少 tpl 或 fp"}
    p = wcp.Pipeline(store=STORE, verbose=False)
    return p.set_label(tpl, fp, status=body.get("status"),
                       starred=body.get("starred"), note=body.get("note"))


@app.get("/api/pipeline/expand")
def api_pipeline_expand(q):
    """同义词扩展预览：让用户看到"我搜这个词会顺带匹配到什么"。"""
    import wcpipeline as wcp
    return wcp.expand_query((q.get("q") or [""])[0])


@app.post("/api/pipeline/run")
def api_pipeline_run(body):
    tpl = body.get("tpl") or ""
    if not tpl:
        return {"error": "缺少 tpl"}
    if not app.run_bg(_pipeline_worker, body):
        return {"error": "已有任务在跑"}
    return {"ok": True}


def _pipeline_worker(body):
    import wcpipeline as wcp
    p = wcp.Pipeline(store=STORE, verbose=False)
    p._log = app.progress
    chats = body.get("chats") or None
    # top=0 表示不限制会话数（用户在界面上已经自己多选过了）
    top = int(body.get("top") or 0)
    return p.run(body.get("tpl"), chats=chats,
                 pattern=body.get("pattern") or "",
                 days=int(body.get("days") or 180),
                 top=top if top else 999,
                 max_chunks=int(body.get("chunks") or 60))


@app.get("/api/csv")
def api_csv(q):
    import wcpipeline as wcp
    tpl = (q.get("tpl") or [""])[0]
    p = wcp.Pipeline(store=STORE, verbose=False)
    path = p.export_csv(tpl)
    with open(path, "rb") as f:
        return f.read()


# ---------------- 年度总报告（结合所有聊天）----------------

@app.get("/api/year")
def api_year(q):
    import wcyear
    y = (q.get("year") or [""])[0]
    try:
        y = int(y) if y else None
    except ValueError:
        y = None
    return wcyear.build(STORE, year=y)


# ---------------- 聊天即消费 ----------------
# 这一块**不在这个服务里实现** —— 它是原来那个完整仪表盘
# （额度管理 / API Key 管理 / 模型市场 / 按人塞 Key / 5 个视图：
#   总览·聊天·计费·模型市场·实验室），作为**子应用**跑在 8899，
# 由前端第 4 个标签用 iframe 完整嵌入。
# 所以这里没有 /api/consume —— 之前留了两个接口但删了 wcconsume.py，
# 一调就 500，已经清掉。


@app.get("/api/export/report")
def api_export_report(q):
    """把报告导出成单文件 HTML（可分享/存档）。"""
    cid = (q.get("chat") or [""])[0]
    period = (q.get("period") or ["month"])[0]
    d = report_data(cid, period)
    page = render_export_report(d)
    return page.encode("utf-8")


# ==========================================================================
# 导出用渲染（独立于前端，生成静态单文件）
# ==========================================================================

def render_export_report(d: dict) -> str:
    from wcweb import load_design_css
    css = load_design_css()
    if not d.get("total"):
        body = '<div class="empty">这段时间没有消息</div>'
    else:
        mx = max((x["n"] for x in d["series"]), default=1) or 1
        cols = "".join(
            f'<div class="col"><div class="stack" style="height:'
            f'{max(2, round(x["n"]/mx*100))}%"></div>'
            f'<div class="lbl">{esc(x["label"])}</div></div>'
            for x in d["series"][-60:])
        hmax = max(d["hours"]) or 1
        hcols = "".join(
            f'<div class="col"><div class="stack" style="height:'
            f'{max(2, round(h/hmax*100))}%"><div class="sg-in" '
            f'style="height:100%"></div></div>'
            f'<div class="lbl">{i if i%3==0 else ""}</div></div>'
            for i, h in enumerate(d["hours"]))
        hl = d.get("highlights") or {}
        hls = []
        if hl.get("busiest"):
            hls.append(f'最忙的一天 <b>{esc(hl["busiest"]["day"])}</b>'
                       f'（{hl["busiest"]["n"]} 条）')
        if hl.get("peak_hour") is not None:
            hls.append(f'最活跃时段 <b>{hl["peak_hour"]:02d}:00</b>')
        if hl.get("late_n"):
            hls.append(f'深夜消息 <b>{hl["late_n"]}</b> 条'
                       f'（占 {hl.get("late_pct")}%）')
        if hl.get("longest"):
            hls.append(f'最长的一条 <b>{hl["longest"]["len"]} 字</b>')
        hls.append(f'我开启话题 <b>{hl.get("init_mine",0)}</b> 次 / '
                   f'对方 <b>{hl.get("init_their",0)}</b> 次')
        body = f"""
<div class="kpis">
  <div class="kpi"><div class="k">消息总数</div><div class="v">{d['total']:,}</div>
    <div class="s">{d['days']} 个活跃天</div></div>
  <div class="kpi"><div class="k">我发出的</div><div class="v green">{d['n_out']:,}</div>
    <div class="s">占 {round(d['n_out']/d['total']*100)}%</div></div>
  <div class="kpi"><div class="k">对方发的</div><div class="v blue">{d['n_in']:,}</div></div>
  <div class="kpi"><div class="k">字数</div><div class="v amber">{d['chars']:,}</div></div>
  <div class="kpi"><div class="k">Token</div><div class="v violet">{d['tok']:,}</div></div>
</div>
<div class="section"><div class="card">
  <div class="card-head"><span>消息量走势</span></div>
  <div class="chart">{cols}</div></div></div>
<div class="section grid g2">
  <div class="card"><div class="card-head"><span>24 小时作息</span></div>
    <div class="chart" style="height:120px">{hcols}</div></div>
  <div class="card"><div class="card-head"><span>高光时刻</span></div>
    <div class="list">{''.join(f'<div class="item"><div class="body"><div class="t1">{x}</div></div></div>' for x in hls)}</div></div>
</div>"""
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>聊天报告 · {esc(d['name'])} · {esc(d['title'])}</title>
<style>{css}</style></head><body>
<div class="bg-glow"></div>
<div class="top"><div class="brand"><div class="logo">📊</div>
  <div><h1>{esc(d['name'])}</h1>
    <div class="sub">{esc(d['title'])} · 导出于 {time.strftime('%Y-%m-%d %H:%M')}</div>
  </div></div></div>
<div class="page">{body}
<div class="report-foot">由「微信洞察」生成 · 含私人内容，分享前请确认</div>
</div></body></html>"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="统一洞察页面")
    ap.add_argument("--port", type=int, default=8772)
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    app.port = a.port
    app.set_page(PAGE)
    app.serve(open_browser=a.open,
              banner_extra="聊天报告 / 关系分析 / 业务线")
