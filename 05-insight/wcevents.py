# -*- coding: utf-8 -*-
"""
wcevents.py — 事件抽取引擎（关系分析的新核心）

═══════════════════════════════════════════════════════════════════
  为什么要改成事件驱动
═══════════════════════════════════════════════════════════════════
  旧做法是"算指标 → 让 LLM 解读指标"，问题在于：
    · 指标是抽象的，用户无法验证（"回避信号 13.6 字 vs 8.2 字"是啥意思？）
    · 结论悬空，看不出具体指哪件事

  新做法：**先抽事件，再用事件说话**
    · 每个事件有：类型、时间、谁触发、一句话概要、原文片段
    · 用户点开事件 → 看原文上下文 → 自己核对
    · 结论必须建立在**具体事件**上（"3 次冲突里 2 次是他先低头"）

═══════════════════════════════════════════════════════════════════
  每类事件的抽取上限（用户明确要求）
═══════════════════════════════════════════════════════════════════
  上下文有时候很长，不能无限制地喂给模型。所以：
    · 每类事件有 **每会话上限**（max_per_chat）
    · 每类事件有 **每个块的产出上限**（per_chunk）
    · 抽取完还会按"重要度"裁剪到 max_total

  重要度怎么定：冲突/重要话题 > 惊喜/关心 > 求助/共同活动 > 日常分享
  （越靠前越值得留存，日常分享最多留几条意思一下）
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import hashlib
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

from wcllm import LLM, MODEL_FAST  # noqa: E402

DATA_DB = os.path.join(CORE, "chat.db")

SCHEMA = """
create table if not exists events (
  id        integer primary key autoincrement,
  fp        text unique,
  chat_id   text,
  chat_name text,
  kind      text,           -- 事件类型
  tone      text,           -- 情绪性质：serious/playful/mixed/neutral
  t         integer,
  day       text,
  actor     text,           -- 谁触发：我 / 对方 / 共同
  summary   text,           -- 一句话概要
  detail    text,           -- 稍详细一点的描述
  quote     text,           -- 原文片段
  local_id  integer,        -- 依据消息
  weight    real default 1, -- 重要度（只用于排序展示，不用于截断）
  created   text
);
create index if not exists idx_ev_chat on events(chat_id, t);
create index if not exists idx_ev_kind on events(kind);
create index if not exists idx_ev_t    on events(t);
"""

# 依赖 tone 列的索引要在**迁移之后**才能建 ——
# 老库的 events 表没有 tone 列，放在 SCHEMA 里会在 executescript 阶段就炸
# （踩过：sqlite3.OperationalError: no such column: tone）。
LATE_SCHEMA = """
create index if not exists idx_ev_tone on events(tone);
"""

# ---------------- 事件类型定义 ----------------
#
# ⚠️ **不设抽取上限**（用户明确要求）。
#    原来的 max_per_chat / per_chunk 是为控制 token 成本，
#    但实测一轮几毛钱，为了"看得全、分析得细"不值得省。
#    weight 保留，但只用于**排序展示**，不再用于截断。
EVENT_TYPES = {
    "conflict": {
        "label": "冲突 / 吵架", "icon": "⚡", "weight": 10,
        "desc": "双方发生不愉快。**必须区分真假**：\n"
                "  · 真冲突 = 有实际矛盾、气氛变冷、一方真生气、需要修复\n"
                "  · 假吵架 = 拌嘴、互怼、开玩笑、撒娇式抱怨，语气亲密，没有实际矛盾\n"
                "  两种都要抽出来，但在 tone 字段必须标清楚（serious / playful / mixed）。\n"
                "  **误判代价很大**：把打情骂俏当冲突，会把好关系分析成危机。",
    },
    "important_topic": {
        "label": "重要话题", "icon": "🎯", "weight": 9,
        "desc": "聊到关系定义、未来计划、家人、感情、价值观这类有重量的话题。",
    },
    "avoidance": {
        "label": "回避 / 转移", "icon": "🌀", "weight": 8,
        "desc": "一方问了敏感/重要的问题，另一方**没有正面回答**——"
                "岔开话题、敷衍、只回表情、突然换话题。要保留提问和回避两边的原文。",
    },
    "surprise": {
        "label": "惊喜 / 礼物", "icon": "🎁", "weight": 8,
        "desc": "送礼物、准备惊喜、请客、特意做某件事讨对方开心。",
    },
    "care": {
        "label": "关心 / 照顾", "icon": "💗", "weight": 7,
        "desc": "一方对另一方表达关心、照顾、担心：生病问候、提醒吃饭睡觉、"
                "情绪低落时的安慰。",
    },
    "reassure": {
        "label": "安全感确认", "icon": "🔒", "weight": 7,
        "desc": "一方在寻求关系确认或安全感：「你还在吗」「你是不是不想理我」"
                "「我会不会太黏人」，以及另一方的回应。",
    },
    "help": {
        "label": "求助 / 帮忙", "icon": "🤝", "weight": 6,
        "desc": "一方请另一方帮忙（学业、工作、技术、跑腿），以及后续有没有帮成。",
    },
    "activity": {
        "label": "共同活动", "icon": "🎬", "weight": 6,
        "desc": "双方一起做的事：吃饭、看电影、打球、出去玩、见面。",
    },
    "share": {
        "label": "日常分享", "icon": "📮", "weight": 3,
        "desc": "一方分享生活切片：吃了什么、看到什么、今天的状态。",
    },
}

# 情绪性质：这是区分「真吵架」和「假吵架」的关键字段
TONES = {
    "serious": "认真 —— 有实际矛盾或当真在讨论，气氛是紧的",
    "playful": "玩闹 —— 拌嘴、互怼、开玩笑，语气亲密，没有实际矛盾",
    "mixed": "半真半假 —— 表面像玩笑但底下有真实情绪，或半开玩笑地抱怨",
    "neutral": "平实 —— 平淡叙述，没有明显情绪色彩",
}

KIND_ORDER = sorted(EVENT_TYPES, key=lambda k: -EVENT_TYPES[k]["weight"])


def kinds_info() -> list:
    """事件类型信息（**没有上限了**，limit 字段保留但恒为 0 = 不限制）。"""
    return [{"kind": k, "label": v["label"], "icon": v["icon"],
             "weight": v["weight"], "limit": 0,
             "desc": v["desc"]}
            for k, v in sorted(EVENT_TYPES.items(), key=lambda kv: -kv[1]["weight"])]


def tones_info() -> list:
    return [{"tone": k, "desc": v} for k, v in TONES.items()]


EXTRACT_SYS = """你从微信聊天片段里抽取**具体事件**。只输出 JSON 数组。

【什么是"事件"】
一件**有具体内容、发生在具体时间、能指出当事人做了什么**的事。
不是情绪标签，也不是泛泛的评价。

✅ 合格的事件：
  {"kind":"conflict","tone":"serious","summary":"因为迟到吵了一架，他最后道歉了"}
  {"kind":"conflict","tone":"playful","summary":"他骂我混蛋，是在闹着玩"}
  {"kind":"care","tone":"neutral","summary":"她发烧，他隔几小时问一次体温"}
  {"kind":"avoidance","tone":"mixed","summary":"她问「我们算什么」，他岔开去说游戏"}

❌ 不合格（不要输出）：
  {"kind":"conflict","summary":"两人关系不好"}     ← 没有具体事
  {"kind":"care","summary":"他很关心她"}           ← 没有具体行为
  闲聊、表情、广告、通知、纯问候（"在吗""吃了吗"）

【类型只能从这些里选】
%TYPES%

【tone 字段特别重要 —— 必须区分真吵架和假吵架】
同一个「你个混蛋」，可能是真生气，也可能是撒娇互怼。

tone 只能从这四个里选：
%TONES%

判断依据：
  · **playful（玩闹）**：双方都在接梗、有表情包、话题很快转开、
    前后文气氛轻松、用了"哈哈""笑死"、称呼亲昵（"你个傻子""滚啊你"）
  · **serious（认真）**：一方真的不高兴、气氛变冷、
    后续有解释/道歉/沉默、话题卡住了、有"算了""别说了"这类退出语
  · **mixed（半真半假）**：表面开玩笑但有真情绪，
    比如"开玩笑的啦"后面还追一句抱怨
  · **neutral（平实）**：普通叙述，没有情绪波澜

⚠️ 这是最容易判错的地方。**拿不准就看后面几条消息的气氛**：
   对方马上用玩笑接回来 → playful；冷场或需要解释 → serious。
   判错了会把好关系分析成危机，或者把真问题当玩笑放过。

【每条格式】
{"kind":"类型",
 "tone":"serious|playful|mixed|neutral",
 "summary":"一句话概要，20字内，说清发生了什么",
 "detail":"稍微展开，40字内，可以有起因经过；没内容就留空",
 "actor":"我|对方|共同",     ← 谁发起/谁先动的
 "quote":"最能代表这件事的原文片段，30字内",
 "msg_index":依据本块第几条消息(从0开始)}

【硬要求】
1. summary 必须是"发生了什么"，不是"关系怎么样"。
2. 严格只依据原文，不许推测动机、不许补原文没有的细节。
3. 同一件事只输出一条，不要拆成多条。
4. 没有事件就返回 []。宁缺勿滥。

只输出 JSON 数组。"""


def _fp(chat_id: str, kind: str, summary: str) -> str:
    s = re.sub(r"[\s，。、！？,.!?~～]", "", (summary or ""))[:30]
    return hashlib.md5(f"{chat_id}|{kind}|{s}".encode()).hexdigest()[:16]


class EventStore:
    def __init__(self, path: str = DATA_DB):
        import sqlite3
        self.con = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.con.execute("pragma journal_mode=WAL")
        self.con.executescript(SCHEMA)
        self._migrate()
        self.con.commit()

    def _migrate(self):
        """给已有的旧表补列。

        `create table if not exists` **不会**修改已存在的表 ——
        老库里的 events 表没有 tone 列，直接插会报 "no column named tone"。
        所以这里显式检查并 alter，**然后再建依赖 tone 的索引**。
        """
        cols = {r[1] for r in self.con.execute("pragma table_info(events)")}
        if "tone" not in cols:
            self.con.execute("alter table events add column tone text")
        self.con.executescript(LATE_SCHEMA)
        # 老数据没有 tone，统一填 neutral，避免前端拿到 None
        self.con.execute("update events set tone='neutral' "
                         "where tone is null or tone=''")

    def stats(self, chat_id: str = "") -> dict:
        if chat_id:
            n = self.con.execute("select count(*) from events where chat_id=?",
                                 (chat_id,)).fetchone()[0]
            kinds = dict(self.con.execute(
                "select kind,count(*) from events where chat_id=? group by kind",
                (chat_id,)))
            tones = dict(self.con.execute(
                "select tone,count(*) from events where chat_id=? group by tone",
                (chat_id,)))
            return {"total": n, "kinds": kinds, "tones": tones}
        return {"total": self.con.execute("select count(*) from events").fetchone()[0]}

    def list(self, chat_id: str, kinds: list | None = None, days: int = 0,
             limit: int = 300, kind: str = "", tone: str = "",
             sort: str = "time") -> list:
        """列事件。

        sort:
          "time"  —— **默认**，按时间倒序（用户要求"全部"要按时间顺序）
          "weight" —— 按重要度（冲突/重要话题优先），用于"最值得看的"场景
        """
        where, args = ["chat_id=?"], [chat_id]
        if kind:
            where.append("kind=?")
            args.append(kind)
        elif kinds:
            where.append("kind in (%s)" % ",".join("?" * len(kinds)))
            args += kinds
        if tone:
            where.append("tone=?")
            args.append(tone)
        if days:
            where.append("t>=?")
            args.append(int(time.time()) - days * 86400)
        order = ("order by t desc, id desc" if sort == "time"
                 else "order by weight desc, t desc")
        sql = ("select id,kind,tone,t,day,actor,summary,detail,quote,local_id,weight "
               f"from events where {' and '.join(where)} {order} limit ?")
        args.append(limit)
        out = []
        for r in self.con.execute(sql, args):
            out.append({"id": r[0], "kind": r[1], "tone": r[2], "t": r[3],
                        "day": r[4], "actor": r[5], "summary": r[6],
                        "detail": r[7], "quote": r[8], "local_id": r[9],
                        "weight": r[10],
                        "label": EVENT_TYPES.get(r[1], {}).get("label", r[1]),
                        "icon": EVENT_TYPES.get(r[1], {}).get("icon", "•"),
                        "tone_label": {"serious": "认真", "playful": "玩闹",
                                       "mixed": "半真半假", "neutral": "平实"}
                                      .get(r[2], "平实")})
        return out

    def clear(self, chat_id: str) -> int:
        n = self.con.execute("select count(*) from events where chat_id=?",
                             (chat_id,)).fetchone()[0]
        self.con.execute("delete from events where chat_id=?", (chat_id,))
        self.con.commit()
        return n


def extract_chat(chat_id: str, days: int = 0, chunk: int = 50,
                 max_chunks: int = 0,
                 llm: LLM = None, store: EventStore = None, verbose: bool = True,
                 progress=None) -> dict:
    """对单个会话抽事件。

    ⚠️ **不再设任何上限**（用户明确要求）：
      · max_chunks=0 表示**不限制块数**（全部消息都过一遍）
      · 没有 per_chunk / max_per_chat 截断
      · 也**不再做关键词预筛** —— 预筛会漏掉不含关键词的真事件

    ⚠️⚠️ **必须从最新往回抽**（这是修过的一个严重 bug）：
      原来按 `order by local_id`（从最早开始）+ max_chunks 截断，
      结果**永远只抽到最早的 N 块**，最近几个月一条都进不来 ——
      实测事件最新日期停在 5 月，而库里有到 9 月。
      用户反馈"看不到近期情况、天天有消息的群却没提炼出来"就是这个。
      修法：`order by local_id desc`，配合分块后再把块内顺序倒回来
      （块内仍按时间正序，模型才读得懂对话）。
    """
    import sqlite3
    from wcstore import Store
    s = Store(verbose=False)
    es = store or EventStore()
    llm = llm or LLM(model=MODEL_FAST)

    def log(m):
        if verbose:
            print(m, flush=True)
        if progress:
            try:
                progress(m)
            except Exception:
                pass

    nm = s.con.execute("select name from chats where chat_id=?",
                       (chat_id,)).fetchone()
    chat_name = nm[0] if nm else chat_id[:16]

    where, args = ["chat_id=?", "kind='text'"], [chat_id]
    if days:
        where.append("t>=?")
        args.append(int(time.time()) - days * 86400)
    # 从最新往回取
    rows = list(s.con.execute(
        "select local_id,t,direction,sender_name,text from messages "
        f"where {' and '.join(where)} order by local_id desc", args))
    if not rows:
        return {"ok": True, "events": 0, "chunks": 0, "skipped": 0}

    type_lines = "\n".join(
        f'  · {k}（{v["label"]}）：{v["desc"]}'
        for k, v in sorted(EVENT_TYPES.items(), key=lambda kv: -kv[1]["weight"]))
    tone_lines = "\n".join(f'  · {k}：{v}' for k, v in TONES.items())
    sys_p = (EXTRACT_SYS.replace("%TYPES%", type_lines)
             .replace("%TONES%", tone_lines))

    t0 = time.time()
    n_new = n_chunk = n_skip = n_dup = 0
    per_type, per_tone = {}, {}
    for i in range(0, len(rows), chunk):
        if max_chunks and n_chunk >= max_chunks:
            break
        # rows 是"从新到旧"，所以切出来的块也是从新到旧；
        # 但块**内部**要倒回时间正序，否则模型读到的对话是反的。
        block = sorted(rows[i:i + chunk], key=lambda r: r[0])
        if len(block) < 6:
            continue
        n_chunk += 1
        lines = []
        for j, (lid, t, d, sn, tx) in enumerate(block):
            who = "我" if d == "out" else (sn or "对方")
            lines.append(f"[{j}] {time.strftime('%m-%d %H:%M', time.localtime(t))} "
                         f"{who}: {(tx or '')[:110]}")
        try:
            arr = llm.chat_json(sys_p, f"会话：{chat_name}\n\n" + "\n".join(lines),
                                model=MODEL_FAST, max_tokens=2500)
        except Exception as e:
            log(f"    ! 抽取失败 {type(e).__name__}: {e}")
            continue
        if not isinstance(arr, list):
            continue
        # 每类在这个块里的产出上限
        got = {}
        for it in arr:
            if not isinstance(it, dict):
                continue
            kind = str(it.get("kind") or "").strip()
            if kind not in EVENT_TYPES:
                continue
            spec = EVENT_TYPES[kind]
            summary = str(it.get("summary") or "").strip()
            if not summary or len(summary) > 40:
                continue
            # tone：非法值一律归为 neutral，不要让脏数据混进来
            tone = str(it.get("tone") or "").strip().lower()
            if tone not in TONES:
                tone = "neutral"
            per_type[kind] = per_type.get(kind, 0) + 1
            per_tone[tone] = per_tone.get(tone, 0) + 1
            idx = it.get("msg_index")
            try:
                idx = max(0, min(int(idx), len(block) - 1))
            except (TypeError, ValueError):
                idx = 0
            lid, ts, d, sn, tx = block[idx]
            fp = _fp(chat_id, kind, summary)
            cur = es.con.execute(
                "insert or ignore into events"
                "(fp,chat_id,chat_name,kind,tone,t,day,actor,summary,detail,quote,"
                "local_id,weight,created) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fp, chat_id, chat_name, kind, tone, ts,
                 time.strftime("%Y-%m-%d", time.localtime(ts)),
                 str(it.get("actor") or "")[:4], summary,
                 str(it.get("detail") or "")[:80],
                 str(it.get("quote") or tx or "")[:140],
                 lid, spec["weight"], time.strftime("%Y-%m-%d %H:%M:%S")))
            if cur.rowcount:
                n_new += 1
            else:
                n_dup += 1
        es.con.commit()
        if n_chunk % 10 == 0:
            log(f"    …已处理 {n_chunk} 块，抽出 {n_new} 个事件")

    cost = llm.cost()
    return {"ok": True, "chat": chat_name, "events": n_new, "dup": n_dup,
            "chunks": n_chunk, "by_type": per_type, "by_tone": per_tone,
            "seconds": round(time.time() - t0, 1), "cost": cost["cost"]}


def estimate_cost(chat_id: str, days: int = 0, chunk: int = 50) -> dict:
    """预估抽取成本（不调用 LLM），供界面提示用户。"""
    from wcstore import Store
    s = Store(verbose=False)
    where, args = ["chat_id=?", "kind='text'"], [chat_id]
    if days:
        where.append("t>=?")
        args.append(int(time.time()) - days * 86400)
    n_msg = s.con.execute(
        "select count(*) from messages where " + " and ".join(where), args).fetchone()[0]
    # 不再做关键词预筛，所以**每块都要送**，成本按全部块算
    n_chunks = (n_msg + chunk - 1) // chunk if n_msg else 0
    return {"messages": n_msg, "chunks_total": n_chunks, "chunks_hit": n_chunks,
            "est_seconds": round(n_chunks * 1.6), "est_cost": round(n_chunks * 0.00025, 3),
            "note": "已取消关键词预筛与各类上限，全部消息都会过一遍"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="事件抽取")
    ap.add_argument("cmd", nargs="?", default="types",
                    choices=["types", "extract", "list", "stats", "estimate", "clear"])
    ap.add_argument("chat", nargs="?", default="")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--max-chunks", type=int, default=80)
    a = ap.parse_args()

    if a.cmd == "types":
        print("事件类型（按重要度排序，含每会话上限）:")
        for x in kinds_info():
            print(f"  {x['icon']} {x['kind']:<16}{x['label']:<12}"
                  f"权重{x['weight']:<3} 上限{x['limit']:<3}")
            print(f"      {x['desc'][:70]}")
        raise SystemExit(0)

    from wcstore import Store
    s = Store(verbose=False)
    es = EventStore()
    if a.cmd == "estimate":
        hits = s.chat_id_by_name(a.chat)
        if not hits:
            raise SystemExit("找不到会话")
        print(estimate_cost(hits[0]["chat_id"], days=a.days))
    elif a.cmd == "extract":
        hits = s.chat_id_by_name(a.chat)
        if not hits:
            raise SystemExit("找不到会话")
        print(extract_chat(hits[0]["chat_id"], days=a.days, max_chunks=a.max_chunks))
    elif a.cmd == "list":
        hits = s.chat_id_by_name(a.chat)
        if not hits:
            raise SystemExit("找不到会话")
        for e in es.list(hits[0]["chat_id"], days=a.days):
            print(f"  [{e['day']}] {e['icon']} {e['label']:<10} "
                  f"{e['summary'][:40]}")
    elif a.cmd == "clear":
        hits = s.chat_id_by_name(a.chat)
        if hits:
            print("清除了", es.clear(hits[0]["chat_id"]), "个事件")
    print("统计:", es.stats(hits[0]["chat_id"] if a.chat and 'hits' in dir() else ""))
