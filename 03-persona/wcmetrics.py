# -*- coding: utf-8 -*-
"""
wcmetrics.py — 关系指标计算（03-persona 的数据层）

把 kb/relationship_methodology.md 里的每个概念变成**能算的数字**。
所有指标都注明出处（哪个理论/研究），并给出"说明什么、不能说明什么"。

═══════════════════════════════════════════════════════════════════
  为什么指标要这么设计
═══════════════════════════════════════════════════════════════════
  聊天记录只有"谁、什么时候、说了什么"，没有语气、表情、眼神。
  所以：
   · 能算的：频次、节奏、长度变化、词汇倾向、方向不对称
   · 不能算的：真实情绪、真实意图、关系好坏
  每个指标都必须配一句"它不能说明什么"，否则用的人一定会过度解读。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "00-core")
for p in (CORE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from wcstore import Store  # noqa: E402

# ---------------- 词表（Gottman 四骑士的可计算化）----------------

# 批评：针对"人"的概括性指责（不是针对具体行为）
CRITICISM_PAT = re.compile(
    r"你(?:总是|老是|从来|一直|永远|每次都|根本不|压根|完全不)|"
    r"你这个人|你就是|你这种|你这种人|没一次|从没|从来没有")

# 蔑视：居高临下、嘲讽
CONTEMPT_WORDS = ["呵呵", "呵", "切", "无语", "服了", "醉了", "离谱", "搞笑",
                  "就这", "笑死", "幼稚", "有病", "神经", "傻子", "废物", "蠢"]

# 防御：反击 / 推卸
DEFENSE_PAT = re.compile(
    r"是你(?:先|才)|我又(?:没|不)|怪我|关我|我才(?:没|不)|"
    r"凭什么|你自己|你也不|还不是你|你先")

# 冷战信号：敷衍与退出
BRUSH_OFF = {"嗯", "哦", "好", "行", "知道了", "随便", "算了", "没事", "。"}

# 正面互动（情感账户的"存款"）
POSITIVE_PAT = re.compile(
    r"谢谢|感谢|辛苦|麻烦你|不好意思|抱歉|对不起|"
    r"厉害|不错|不赖|真好|好棒|喜欢|可爱|好看|爱了|"
    r"哈哈|嘿嘿|笑死|乐|开心|高兴|太好了|"
    r"注意|小心|早点|休息|保重|吃了吗|吃饭没|多穿|别累|加油|"
    r"想你|想见|见面|一起|陪")

# 负面互动（"取款"）
NEGATIVE_PAT = re.compile(
    r"烦|讨厌|滚|闭嘴|别烦|少来|闹|生气|火大|无语|受不了|"
    r"够了|算了|随便你|爱怎样|不管|懒得|恶心|垃圾|废")

# 情感邀约（bids）：分享类、无明确问题的话题开场
BID_PAT = re.compile(
    r"你看|看看这个|给你看|分享|发现|忽然|突然想到|刚看到|"
    r"推荐|安利|你听|你猜|你知道吗|我跟你说|和你讲")

# 情绪深度话题
DEEP_PAT = re.compile(
    r"喜欢|讨厌|害怕|担心|难过|开心|失落|焦虑|压力|"
    r"未来|打算|计划|以后|将来|梦想|想去|想要|"
    r"家里|爸妈|家人|感情|对象|恋爱|分手|在一起|"
    r"为什么|感觉|其实我|说实话|心里")


def _day(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _pct(a: float, b: float) -> float:
    return round(a / b * 100, 1) if b else 0.0


class RelationMetrics:
    """算一个人（或一个群）的关系指标。"""

    def __init__(self, store: Store = None):
        self.s = store or Store(verbose=False)

    # ---------------- 基础取样 ----------------

    def messages(self, chat_id: str, days: int = 0, kinds=("text",)) -> list:
        where = ["chat_id=?"]
        args = [chat_id]
        if kinds:
            where.append("kind in (%s)" % ",".join("?" * len(kinds)))
            args += list(kinds)
        if days:
            where.append("t >= ?")
            args.append(int(time.time()) - days * 86400)
        sql = ("select local_id,t,day,direction,text from messages "
               f"where {' and '.join(where)} order by t")
        return [{"local_id": r[0], "t": r[1], "day": r[2], "dir": r[3],
                 "text": r[4] or ""} for r in self.s.con.execute(sql, args)]

    # ---------------- 各项指标 ----------------

    def basics(self, chat_id: str, days: int = 0) -> dict:
        msgs = self.messages(chat_id, days)
        if not msgs:
            return {}
        out = [m for m in msgs if m["dir"] == "out"]
        inc = [m for m in msgs if m["dir"] == "in"]
        days_set = {m["day"] for m in msgs}
        # 会话首条是几点开始的
        first_by_day = {}
        for m in msgs:
            first_by_day.setdefault(m["day"], m)
        init_mine = sum(1 for m in first_by_day.values() if m["dir"] == "out")
        init_their = len(first_by_day) - init_mine
        return {
            "total": len(msgs), "out": len(out), "in": len(inc),
            "days": len(days_set),
            "span_days": len(days_set),
            "avg_len_out": round(statistics.mean([len(m["text"]) for m in out]), 1) if out else 0,
            "avg_len_in": round(statistics.mean([len(m["text"]) for m in inc]), 1) if inc else 0,
            "init_mine": init_mine, "init_their": init_their,
            "init_ratio": round(init_mine / max(init_their, 1), 2),
            "first_day": min(days_set), "last_day": max(days_set),
        }

    def magic_ratio(self, chat_id: str, days: int = 0) -> dict:
        """Gottman 正面/负面互动比（稳定关系参考值 5:1）。

        ⚠️ 不能说明什么：这个词表是通用中文，说话风格差异很大
        （比如有人习惯用"笑死"表示好笑，不是嘲讽）。
        所以比值只做参考，**要结合具体消息看**。
        """
        msgs = self.messages(chat_id, days)
        pos = neg = 0
        pos_eg, neg_eg = [], []
        for m in msgs:
            t = m["text"]
            if POSITIVE_PAT.search(t):
                pos += 1
                if len(pos_eg) < 4:
                    pos_eg.append(t[:40])
            if NEGATIVE_PAT.search(t):
                neg += 1
                if len(neg_eg) < 4:
                    neg_eg.append(t[:40])
        ratio = round(pos / max(neg, 1), 2)
        return {"positive": pos, "negative": neg, "ratio": ratio,
                "pos_examples": pos_eg, "neg_examples": neg_eg,
                "verdict": ("健康（≥5）" if ratio >= 5 else
                            "需留意（1~5）" if ratio >= 1 else "负面占主导（<1）"),
                "caveat": "词表是通用中文，个人表达习惯差异大，只做参考"}

    def four_horsemen(self, chat_id: str, days: int = 0) -> dict:
        """Gottman 四骑士：批评 / 蔑视 / 防御 / 冷战。"""
        msgs = self.messages(chat_id, days)
        n = len(msgs) or 1
        crit = [m for m in msgs if CRITICISM_PAT.search(m["text"])]
        cont = [m for m in msgs if any(w in m["text"] for w in CONTEMPT_WORDS)]
        defn = [m for m in msgs if DEFENSE_PAT.search(m["text"])]
        brush = [m for m in msgs if m["dir"] == "in"
                 and m["text"].strip() in BRUSH_OFF]
        # 冷战信号：对方连发 3 条以上无回应
        stone = 0
        run = 0
        for m in msgs:
            if m["dir"] == "in":
                run += 1
            else:
                if run >= 3:
                    stone += 1
                run = 0
        if run >= 3:
            stone += 1
        return {
            "criticism": {"n": len(crit), "rate": _pct(len(crit), n),
                          "eg": [m["text"][:44] for m in crit[:3]]},
            "contempt": {"n": len(cont), "rate": _pct(len(cont), n),
                         "eg": [m["text"][:44] for m in cont[:3]]},
            "defensiveness": {"n": len(defn), "rate": _pct(len(defn), n),
                              "eg": [m["text"][:44] for m in defn[:3]]},
            "stonewalling": {"brush_off": len(brush), "long_gaps": stone,
                             "rate": _pct(len(brush) + stone, n)},
            "antidotes": {
                "criticism": "改成「温柔开场」：说具体行为 + 我的感受 + 我需要什么",
                "contempt": "建立欣赏文化：每天具体地表达一次欣赏（用「不赖」）",
                "defensiveness": "先承担 5% 的责任，哪怕只有一点点",
                "stonewalling": "情绪过载时暂停 20 分钟，但要说清楚「我待会回来」",
            },
        }

    def bids(self, chat_id: str, days: int = 0) -> dict:
        """情感邀约回应率（turn_rate）—— 方法论里认为最灵敏的单一指标。

        bids = 对方发起的「分享类」消息
        turned_towards = 我之后有实质回应（不是"嗯""哦"这类）
        """
        msgs = self.messages(chat_id, days)
        bids, turned, away = 0, 0, 0
        bid_examples = []
        for i, m in enumerate(msgs):
            if m["dir"] != "in":
                continue
            t = m["text"]
            # 分享类邀约：显式分享词，或"带内容但无提问"的消息
            is_bid = bool(BID_PAT.search(t)) or (
                len(t) >= 6 and not t.endswith(("?", "？"))
                and not re.search(r"吗|几点|在哪|怎么|为什么|什么时候", t))
            if not is_bid:
                continue
            bids += 1
            # 看接下来 3 条里我有没有实质回应
            replied = False
            for j in range(i + 1, min(i + 4, len(msgs))):
                nm = msgs[j]
                if nm["dir"] == "in":
                    continue
                if nm["text"].strip() in BRUSH_OFF:
                    replied = False
                    break
                if len(nm["text"].strip()) >= 2:
                    replied = True
                break
            if replied:
                turned += 1
            else:
                away += 1
                if len(bid_examples) < 4:
                    bid_examples.append(t[:44])
        return {"bids": bids, "turned_towards": turned, "turned_away": away,
                "turn_rate": _pct(turned, bids),
                "missed_examples": bid_examples,
                "caveat": "「实质回应」是按长度和敷衍词判断的近似，"
                          "会漏掉简短的走心回复"}

    def latency(self, chat_id: str, days: int = 0, cap_min: int = 720) -> dict:
        """回复节奏。按小时统计中位间隔。

        ⚠️ 关键：慢回 ≠ 不在乎。工作时段、作息差异都会造成慢回，
        所以除了总体还要给**按时段**的分布，让人自己判断。
        """
        msgs = self.messages(chat_id, days)
        gaps_mine, gaps_their = [], []
        hourly_mine = {h: [] for h in range(24)}
        last_in = None
        last_out = None
        for m in msgs:
            if m["dir"] == "in":
                if last_out is not None:
                    d = (m["t"] - last_out) / 60
                    if 0 < d <= cap_min:
                        gaps_their.append(d)
                        hourly_mine[datetime.fromtimestamp(m["t"]).hour].append(d)
                last_in = m["t"]
            else:
                if last_in is not None:
                    d = (m["t"] - last_in) / 60
                    if 0 < d <= cap_min:
                        gaps_mine.append(d)
                last_out = m["t"]

        def stat(a):
            if not a:
                return {}
            a2 = sorted(a)
            return {"n": len(a2), "median_min": round(statistics.median(a2), 1),
                    "p25": round(a2[len(a2) // 4], 1),
                    "p75": round(a2[len(a2) * 3 // 4], 1)}

        # 白天 vs 深夜
        def split(a):
            day = [x for x in a if x <= 30]
            return {"≤30分钟": _pct(len(day), len(a)), ">2小时": _pct(len([x for x in a if x > 120]), len(a))}

        return {"mine": stat(gaps_mine), "theirs": stat(gaps_their),
                "mine_split": split(gaps_mine), "theirs_split": split(gaps_their),
                "caveat": "慢回受作息和工作时段影响，绝对值无意义，"
                          "要看双方差异与趋势"}

    def trends(self, chat_id: str, weeks: int = 12) -> list:
        """按周的趋势：消息量、平均长度、正面比、主动性。

        这是最有价值的一张图 —— 单看某一周没意义，看走势才有意义。
        """
        now = time.time()
        out = []
        for w in range(weeks - 1, -1, -1):
            t1 = now - w * 7 * 86400
            t0 = t1 - 7 * 86400
            msgs = [m for m in self.messages(chat_id)
                    if t0 <= m["t"] < t1]
            if not msgs:
                out.append({"week": time.strftime("%m/%d", time.localtime(t0)),
                            "n": 0, "out": 0, "avg_len": 0, "pos_ratio": 0,
                            "days": 0, "init_mine": 0})
                continue
            o = [m for m in msgs if m["dir"] == "out"]
            pos = sum(1 for m in msgs if POSITIVE_PAT.search(m["text"]))
            neg = sum(1 for m in msgs if NEGATIVE_PAT.search(m["text"]))
            by_day = {}
            for m in msgs:
                by_day.setdefault(m["day"], m)
            im = sum(1 for m in by_day.values() if m["dir"] == "out")
            out.append({
                "week": time.strftime("%m/%d", time.localtime(t0)),
                "n": len(msgs), "out": len(o),
                "avg_len": round(statistics.mean([len(m["text"]) for m in msgs]), 1),
                "pos_ratio": round(pos / max(pos + neg, 1) * 100, 1),
                "days": len(by_day), "init_mine": im,
            })
        return out

    def attachment_signals(self, chat_id: str, days: int = 0) -> dict:
        """依恋倾向的**信号**（不是类型判定）。

        ⚠️ 依恋类型是关系中的状态，不是固定人格，这里只给倾向信号。
        """
        msgs = self.messages(chat_id, days)
        inc = [m for m in msgs if m["dir"] == "in"]
        out = [m for m in msgs if m["dir"] == "out"]
        # 焦虑信号（对方）：追问、确认
        anx = [m for m in inc if re.search(
            r"你怎么不|不理我|在吗.*在吗|是不是|你还(?:在|理)|"
            r"为什么不回|生气了吗|讨厌我", m["text"])]
        # 回避信号（对方）：亲密话题变短
        deep_in = [m for m in inc if DEEP_PAT.search(m["text"])]
        normal_in = [m for m in inc if not DEEP_PAT.search(m["text"])]
        deep_len = statistics.mean([len(m["text"]) for m in deep_in]) if deep_in else 0
        norm_len = statistics.mean([len(m["text"]) for m in normal_in]) if normal_in else 0
        avoid_ratio = round(deep_len / norm_len, 2) if norm_len else 0
        return {
            "anxiety_signal": {"n": len(anx), "rate": _pct(len(anx), len(inc) or 1),
                               "eg": [m["text"][:40] for m in anx[:3]]},
            "avoidance_signal": {
                "deep_topic_msgs": len(deep_in), "normal_msgs": len(normal_in),
                "deep_avg_len": round(deep_len, 1), "normal_avg_len": round(norm_len, 1),
                "ratio": avoid_ratio,
                "note": ("亲密话题时消息明显变短（<0.7）→ 可能有回避倾向"
                         if avoid_ratio and avoid_ratio < 0.7 else
                         "亲密话题与普通话题长度接近，无明显回避信号")},
            "caveat": "这只是聊天字数层面的信号，不能当依恋类型诊断；"
                      "字数受话题性质影响很大（比如讨论作业天然更长）",
        }

    def deep_topic_ratio(self, chat_id: str, days: int = 0) -> dict:
        msgs = self.messages(chat_id, days)
        deep = [m for m in msgs if DEEP_PAT.search(m["text"])]
        return {"deep": len(deep), "total": len(msgs),
                "ratio": _pct(len(deep), len(msgs))}

    # ---------------- 汇总 ----------------

    def all(self, chat_id: str, days: int = 0) -> dict:
        nm = self.s.con.execute("select name,is_group from chats where chat_id=?",
                                (chat_id,)).fetchone()
        return {
            "chat_id": chat_id,
            "name": nm[0] if nm else chat_id[:16],
            "is_group": bool(nm[1]) if nm else False,
            "window_days": days or None,
            "basics": self.basics(chat_id, days),
            "magic_ratio": self.magic_ratio(chat_id, days),
            "four_horsemen": self.four_horsemen(chat_id, days),
            "bids": self.bids(chat_id, days),
            "latency": self.latency(chat_id, days),
            "attachment": self.attachment_signals(chat_id, days),
            "deep_topic": self.deep_topic_ratio(chat_id, days),
            "trends": self.trends(chat_id),
        }


def candidate_chats(store: Store, min_msgs: int = 300, top: int = 20) -> list:
    """挑适合做关系分析的会话（消息量够大、最好是单聊）。"""
    rows = store.con.execute(
        "select chat_id,name,is_group,n_total,last_t from chats "
        "where n_total>=? order by n_total desc limit ?", (min_msgs, top)).fetchall()
    return [{"chat_id": r[0], "name": r[1] or r[0][:16], "is_group": bool(r[2]),
             "n": r[3],
             "last": time.strftime("%Y-%m-%d", time.localtime(r[4])) if r[4] else ""}
            for r in rows]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="关系指标")
    ap.add_argument("chat", nargs="?", default="", help="会话名或 chat_id")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--list", action="store_true", help="列出候选会话")
    a = ap.parse_args()

    s = Store(verbose=False)
    if a.list or not a.chat:
        print("候选会话（消息量 Top）:")
        for c in candidate_chats(s):
            tag = "群" if c["is_group"] else "单聊"
            print(f"  {c['name'][:24]:<26}{tag:<5}{c['n']:>7} 条  最后 {c['last']}")
        raise SystemExit(0)

    r = RelationMetrics(s)
    hits = s.chat_id_by_name(a.chat)
    if not hits:
        raise SystemExit(f"找不到会话 {a.chat!r}")
    cid = hits[0]["chat_id"]
    d = r.all(cid, days=a.days)
    b = d["basics"]
    print(f"\n{'='*66}\n{d['name']}  （{b['total']} 条文本 · {b['days']} 天）\n{'='*66}")
    print(f"我发出 {b['out']} / 收到 {b['in']}   平均长度 我{b['avg_len_out']}字 对方{b['avg_len_in']}字")
    print(f"主动性：我开启 {b['init_mine']} 天 / 对方 {b['init_their']} 天（比值 {b['init_ratio']}）")
    mr = d["magic_ratio"]
    print(f"\n正面:负面 = {mr['positive']}:{mr['negative']} = {mr['ratio']}  → {mr['verdict']}")
    fh = d["four_horsemen"]
    print(f"四骑士：批评{fh['criticism']['rate']}% 蔑视{fh['contempt']['rate']}% "
          f"防御{fh['defensiveness']['rate']}% 冷战率{fh['stonewalling']['rate']}%")
    bi = d["bids"]
    print(f"情感邀约回应率 turn_rate = {bi['turn_rate']}%（{bi['turned_towards']}/{bi['bids']}）")
    print(f"回复节奏：我中位 {d['latency']['mine'].get('median_min')} 分钟 / "
          f"对方 {d['latency']['theirs'].get('median_min')} 分钟")
    print(f"深度话题占比 {d['deep_topic']['ratio']}%")
