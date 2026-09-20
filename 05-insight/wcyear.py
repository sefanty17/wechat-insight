# -*- coding: utf-8 -*-
"""
wcyear.py — 全局年度报告（结合**所有**聊天的总报告）

设计目标（用户要求）：像 QQ 年度报告那样**有趣、好看**，
不是冷冰冰的统计表。所以除了数字，还要有：
  · 有代入感的叙述（"这一年你发了 X 条消息"）
  · 趣味称号（"深夜话痨""秒回之王"）
  · 排行与对比（谁最黏你、你最爱找谁）
  · 个人签名（你最常说的词）

统计口径：默认按"年"聚合，但数据只有 2026-01-09 ~ 09-19，
所以实际是"数据范围内"的年度报告。
"""
from __future__ import annotations

import os
import re
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "00-core")
for p in (CORE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

# 停用词（统计口头禅时排掉）
STOP = set("""
的 了 是 我 你 他 她 它 们 在 有 和 就 都 也 还 不 没 很 好 吧 啊 呢 吗 呀 嘛 哦 嗯 一个
什么 怎么 这样 那样 时候 现在 已经 一下 一直 有点 真的 就是 然后 因为 所以 那个 这个
可以 不是 没有 知道 觉得 应该 可能 如果 但是 而且 这些 那些 自己 大家 我们 你们 他们
哈哈 哈哈哈 呜呜 哎呀 我去 我靠 嗯嗯 好的 收到 没事 谢谢 哈哈 在吗 那个 这个 一下
""".split())

# 趣味称号的判定
TITLES = [
    ("night_owl", "深夜话痨", "🌙", "23:00-06:00 的消息占比最高"),
    ("early_bird", "早起冠军", "🌅", "06:00-09:00 的消息占比最高"),
    ("speed_demon", "秒回之王", "⚡", "回复中位间隔不到 1 分钟"),
    ("long_winded", "长篇大王", "📜", "平均消息长度最长"),
    ("short_king", "惜字如金", "✂️", "平均消息长度极短"),
    ("emoji_lover", "表情包富翁", "😄", "表情/图片占比最高"),
    ("social_butterfly", "社交达人", "🦋", "聊过的人最多"),
    ("loyal", "专一选手", "💎", "消息最集中在少数几个人"),
]


def build(store, year: int | None = None, groups: bool = False) -> dict:
    """生成全局年度报告数据。

    ⚠️ 默认**排除群聊**（groups=False）—— 年度总结只算单聊，
    因为群聊是"别人的信息流"，不是"你的人际关系"。
    要按群聊另做一份时传 groups=True。

    排除实现：用 `chat_id not in (select chat_id from chats where is_group=1)`
    而不是 join，因为有些会话在 chats 表里没登记。
    """
    c = store.con
    # 年范围
    if year:
        t0 = int(time.mktime((year, 1, 1, 0, 0, 0, 0, 0, -1)))
        t1 = int(time.mktime((year + 1, 1, 1, 0, 0, 0, 0, 0, -1)))
    else:
        r = c.execute("select min(t), max(t) from messages").fetchone()
        t0, t1 = (r[0] or 0), (r[1] or 0) + 1

    # 两套条件：W 用于不别名的查询，Wm 用于 join 了 chats 的查询
    # （join 场景下 chat_id 会歧义，必须写 m.chat_id）
    def _conds(prefix: str) -> list:
        out = [f"{prefix}t>=?", f"{prefix}t<?"]
        if not groups:
            out.append(f"{prefix}chat_id not in "
                       "(select c2.chat_id from chats c2 where c2.is_group=1)")
            out.append(f"{prefix}chat_id != 'filehelper'")
        return out

    W = " and ".join(_conds(""))       # 不别名的查询用
    Wm = " and ".join(_conds("m."))     # join 了 chats 的查询用（避免歧义）
    # 排除群聊走的是子查询，不增加占位符，所以参数始终是 (t0, t1)
    A = (t0, t1)

    total = c.execute(f"select count(*) from messages where {W}", A).fetchone()[0]
    if not total:
        return {"total": 0, "groups": groups}

    n_out = c.execute(f"select count(*) from messages where {W} and direction='out'",
                      A).fetchone()[0]
    n_in = total - n_out
    chars = c.execute(f"select sum(length(text)) from messages where {W}", A).fetchone()[0] or 0
    tok = c.execute(f"select sum(tok) from messages where {W}", A).fetchone()[0] or 0
    n_chats = c.execute(f"select count(distinct chat_id) from messages where {W}",
                        A).fetchone()[0]
    days = c.execute(f"select count(distinct day) from messages where {W}", A).fetchone()[0]
    span = c.execute(f"select min(t), max(t) from messages where {W}", A).fetchone()

    # 按天
    daily = [{"d": r[0], "n": r[1], "out": r[2]} for r in c.execute(
        f"select day, count(*), sum(case when direction='out' then 1 else 0 end) "
        f"from messages where {W} group by day order by day", A)]
    # 按小时
    hours = [0] * 24
    for r in c.execute(
            f"select strftime('%H',t,'unixepoch','localtime') h, count(*) "
            f"from messages where {W} group by h", A):
        try:
            hours[int(r[0])] = r[1]
        except (TypeError, ValueError):
            pass
    # 按星期
    weekday = [0] * 7
    for r in c.execute(
            f"select strftime('%w',t,'unixepoch','localtime') w, count(*) "
            f"from messages where {W} group by w", A):
        try:
            weekday[int(r[0])] = r[1]
        except (TypeError, ValueError):
            pass

    # 聊得最多的人（排除文件传输助手这类）
    # ⚠️ 这里必须用 Wm（带 m. 前缀）而不是 W —— join 之后 chat_id 会歧义
    people = []
    for r in c.execute(
            f"select m.chat_id, ch.name, ch.is_group, count(*) n, "
            f"sum(case when m.direction='out' then 1 else 0 end) o, "
            f"sum(case when m.direction='in' then 1 else 0 end) i, sum(m.tok) "
            f"from messages m left join chats ch on ch.chat_id=m.chat_id "
            f"where {Wm} group by m.chat_id order by n desc limit 12", A):
        nm = r[1] or r[0][:14]
        if nm in ("文件传输助手",):
            continue
        people.append({"chat_id": r[0], "name": nm, "is_group": bool(r[2]),
                       "n": r[3], "out": r[4], "in": r[5], "tok": round(r[6] or 0)})

    # 谁最黏你 / 你最爱找谁
    #
    # ⚠️ 不能用上面 people 的 top12 来算 —— 那份榜单被总量主导，
    # jang-jawan. 一个人占 89% 的消息量，导致这两个榜只剩 2 条。
    # 这里单独查，门槛降到 30 条，让中小会话也能上榜。
    def _by(direction: str, limit: int = 6) -> list:
        rows = c.execute(
            f"select m.chat_id, ch.name, count(*) n from messages m "
            f"left join chats ch on ch.chat_id=m.chat_id "
            f"where {Wm} and m.direction=? and ch.is_group=0 "
            f"and ch.name is not null and ch.name != '文件传输助手' "
            f"group by m.chat_id having n >= 30 order by n desc limit ?",
            A + (direction, limit)).fetchall()
        return [{"chat_id": r[0], "name": r[1], "n": r[2]} for r in rows]

    clingy = _by("in")      # 对方发给我的
    favorite = _by("out")   # 我发给对方的

    # 消息类型
    kinds = [{"k": r[0], "n": r[1]} for r in c.execute(
        f"select kind, count(*) from messages where {W} group by kind order by 2 desc", A)]

    # 我的口头禅（只统计我发的文本）
    #
    # ⚠️ 踩过的坑：一开始我把所有 2 字滑窗都计入，结果 top 榜全是
    # 「赖不」「还真」这种碎片 —— 它们是「不赖不赖」「还真是」的跨词切片，
    # 不是独立的口头禅。而且「不赖」和「不赖不赖」会同时上榜，重复。
    # 修法：
    #   1. 先用**整条消息**做计数（一条消息 = 一次使用），而不是滑窗
    #   2. 滑窗只作为补充，且**跳过已被更长词覆盖的片段**
    mine_texts = [r[0] for r in c.execute(
        f"select text from messages where {W} and direction='out' and kind='text' "
        f"and length(text) between 2 and 24", A)]
    cnt = Counter()
    for t in mine_texts:
        s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t or "")
        if not s:
            continue
        # 整条就是一个短词（最常见的口头禅形态：「不赖」「可以」「喵」）
        if 2 <= len(s) <= 6 and s not in STOP:
            cnt[s] += 1
        # 长句里的 2 字片段作为补充
        elif len(s) > 6:
            for i in range(len(s) - 1):
                g = s[i:i + 2]
                if g not in STOP:
                    cnt[g] += 1
    # 去掉被更长的高频词包含的碎片（「赖不」被「不赖」包含 → 丢掉）
    ranked = [w for w, n in cnt.most_common(400) if n >= 8]
    drop = set()
    for w in ranked:
        for long_w in ranked:
            if w != long_w and w in long_w and cnt[long_w] >= cnt[w] * 0.6:
                drop.add(w)
                break
    catchphrases = [{"w": w, "n": cnt[w]} for w in ranked if w not in drop][:12]

    # 最长的一条
    lg = c.execute(
        f"select t, direction, text, chat_id from messages where {W} and kind='text' "
        f"order by length(text) desc limit 1", A).fetchone()
    longest = None
    if lg:
        nm = c.execute("select name from chats where chat_id=?", (lg[3],)).fetchone()
        longest = {"t": lg[0], "dir": lg[1], "len": len(lg[2] or ""),
                   "chat": nm[0] if nm else "", "text": (lg[2] or "")[:300]}

    # 最忙的一天 / 最忙的一小时
    busiest = max(daily, key=lambda x: x["n"]) if daily else None
    busiest_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else None
    late_n = sum(hours[23:24]) + sum(hours[0:6])

    # 连续聊天天数（最长）
    streak, best_streak, best_range = 0, 0, ("", "")
    prev = None
    from datetime import datetime, timedelta
    for d in daily:
        cur = datetime.strptime(d["d"], "%Y-%m-%d")
        if prev and (cur - prev).days == 1:
            streak += 1
        else:
            streak = 1
        if streak > best_streak:
            best_streak = streak
            best_range = ((cur - timedelta(days=streak - 1)).strftime("%m/%d"),
                          cur.strftime("%m/%d"))
        prev = cur

    # 称号
    titles = []
    if any(hours):
        night_pct = late_n / total * 100
        if night_pct >= 15:
            titles.append({"key": "night_owl", "name": "深夜话痨", "icon": "🌙",
                           "desc": f"{night_pct:.0f}% 的消息发在 23 点后或凌晨"})
        morning = sum(hours[6:9])
        if morning / total * 100 >= 12:
            titles.append({"key": "early_bird", "name": "早起冠军", "icon": "🌅",
                           "desc": f"{morning/total*100:.0f}% 的消息在 6-9 点"})
    if any(hours):
        ph = max(range(24), key=lambda h: hours[h])
        if ph >= 22 or ph < 3:
            titles.append({"key": "night_owl2", "name": "夜行动物", "icon": "🦉",
                           "desc": f"最活跃时段 {ph:02d}:00"})
    # 回复速度（近似：同一会话内 in→out 的中位间隔）
    gaps = []
    for p in people[:6]:
        rows = list(c.execute(
            "select t,direction from messages where chat_id=? and kind='text' "
            "order by t limit 4000", (p["chat_id"],)))
        last_in = None
        for t, d in rows:
            if d == "in":
                last_in = t
            elif last_in is not None:
                g = (t - last_in) / 60
                if 0 < g <= 720:
                    gaps.append(g)
                last_in = None
    if gaps:
        import statistics
        med = statistics.median(gaps)
        if med < 1:
            titles.append({"key": "speed", "name": "秒回之王", "icon": "⚡",
                           "desc": f"回复中位间隔 {med*60:.0f} 秒"})
        elif med > 30:
            titles.append({"key": "slow", "name": "佛系回复", "icon": "🐢",
                           "desc": f"回复中位间隔 {med:.0f} 分钟"})
    st_n = sum(1 for k in kinds if k["k"] in ("sticker", "image", "video"))
    st_pct = st_n / total * 100
    if st_pct >= 15:
        titles.append({"key": "emoji", "name": "表情包富翁", "icon": "😄",
                       "desc": f"表情/图片占 {st_pct:.0f}%"})
    if n_chats >= 40:
        titles.append({"key": "social", "name": "社交达人", "icon": "🦋",
                       "desc": f"和 {n_chats} 个会话聊过"})
    top3 = sum(p["n"] for p in people[:3])
    if people and top3 / total >= 0.6:
        titles.append({"key": "loyal", "name": "专一选手", "icon": "💎",
                       "desc": f"前 3 个人占了 {top3/total*100:.0f}% 的消息"})

    return {
        "total": total, "n_out": n_out, "n_in": n_in, "chars": chars,
        "tok": round(tok), "chats": n_chats, "days": days,
        "start": time.strftime("%Y-%m-%d", time.localtime(span[0])) if span[0] else "",
        "end": time.strftime("%Y-%m-%d", time.localtime(span[1])) if span[1] else "",
        "daily": daily, "hours": hours, "weekday": weekday,
        "people": people, "clingy": clingy, "favorite": favorite,
        "kinds": kinds, "catchphrases": catchphrases, "longest": longest,
        "busiest": busiest, "busiest_hour": busiest_hour,
        "late_n": late_n, "late_pct": round(late_n / total * 100, 1),
        "streak": best_streak, "streak_range": best_range,
        "titles": titles,
    }


KIND_LABEL = {"text": "文字", "sticker": "表情", "image": "图片", "quote": "引用",
              "voice": "语音", "video": "视频", "app": "链接", "location": "位置",
              "card": "名片", "system": "系统", "unknown": "其他"}


def _fmt(n) -> str:
    n = n or 0
    if n >= 1e8:
        return f"{n/1e8:.2f}亿"
    if n >= 1e4:
        return f"{n/1e4:.1f}万"
    return f"{n:,}"


if __name__ == "__main__":
    from wcstore import Store
    d = build(Store(verbose=False))
    print(f"年度总览 {d['start']} → {d['end']}")
    print(f"  消息 {_fmt(d['total'])} 条（我发 {_fmt(d['n_out'])}）· "
          f"{d['chats']} 个会话 · {d['days']} 个活跃天")
    print(f"  字数 {_fmt(d['chars'])} · token {_fmt(d['tok'])}")
    print(f"  最长连续聊 {d['streak']} 天（{d['streak_range'][0]} ~ {d['streak_range'][1]}）")
    print(f"  称号: {[t['name'] for t in d['titles']]}")
    print(f"  口头禅: {[c['w'] for c in d['catchphrases'][:8]]}")
    print(f"  最忙的一天: {d['busiest']['d']}（{d['busiest']['n']} 条）")
