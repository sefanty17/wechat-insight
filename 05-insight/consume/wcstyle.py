# -*- coding: utf-8 -*-
"""
wcstyle.py — 从真实聊天记录里挖出「你怎么说话」，生成可编辑的风格档案

思路：与其让模型猜你的语气，不如直接从你说过的 10 万条消息里统计出事实——
句子多长、爱用什么词、标点怎么用、表情多不多、对不同人是不是两种语气。

产出 style_persona.md：
  · 一、客观统计（这些都是算出来的，不是编的）
  · 二、口头禅与高频表达
  · 三、语气分档（对谁客气、对谁随意）
  · 四、真实样本（原文片段，给我自己核对）
  · 五、写作规则（给模型看的指令，可以手改）

用法：
  python wcstyle.py                 # 全量重算并写 style_persona.md
  python wcstyle.py --top 60        # 口头禅取前 60 个
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PERSONA_FILE = os.path.join(HERE, "style_persona.md")
STATS_FILE = os.path.join(HERE, "style_stats.json")

# 停用词：这些不是"口头禅"，统计时排掉
STOP = set("""
的 了 是 我 你 他 她 它 们 在 有 和 就 都 也 还 不 没 很 好 吧 啊 呢 吗 呀 嘛 哦 嗯 额
这 那 个 上 下 中 来 去 说 想 要 会 能 可 会 把 被 给 跟 对 从 到 让 但 而 或 与 及
一个 什么 怎么 这样 那样 时候 现在 已经 一下 一直 有点 真的 就是 然后 因为 所以
""".split())

# 语气/情绪词，用来判断风格
PARTICLES = "吧 啊 呢 吗 呀 嘛 哦 嗯 哈 呗 咯 啦 哇 哎 唉 嘿 咦 唔 嘞 滴 呐 噢 诶 喔".split()
LAUGH = re.compile(r"[哈嘿嘻呵]{2,}|哈哈+|233+|hhh+|😂|🤣|😅|😆")

# 「无聊核心」：命中这些的 n-gram 只是"我顺口带的虚词组合"，不算个人特色。
# 注意不能直接把它们丢进 STOP —— STOP 是按整词精确匹配的，
# 而这里要做的是"子串包含"判断（比如 "我的时间" 含 "我的"，也不算特色）。
BORING_CORE = set(list(STOP) + """
我的 你的 他的 她的 我就 你就 我不 你不 我也 你也 有一 有个 一点 一次 一样
这些 那些 不能 不会 不要 应该 需要 觉得 感觉 其实 真是 要是 好了 看看
""".split())


def _msgs_of_mine() -> list:
    """取我说过的所有文本消息，按可信度依次尝试三个来源：

      1. my_texts.jsonl —— 全量原文（`python _dump_my_texts.py` 生成），**最准**
      2. ledger.json —— 账本，但为了体积只存 token、**没有 text**，用不上
      3. flow_cache.json —— 每会话只有最近 500 条，样本偏少

    实测：缓存 4205 条 vs 全量 14246 条，差 3.4 倍，口头禅频次会明显不同。
    """
    path = os.path.join(HERE, "my_texts.jsonl")
    if os.path.exists(path):
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = (r.get("x") or "").strip()
                if t:
                    out.append({"chat": None, "wxid": r.get("s"), "t": r.get("t", 0),
                                "text": t})
        if out:
            return out

    path = os.path.join(HERE, "flow_cache.json")
    if not os.path.exists(path):
        raise SystemExit(
            "没有 my_texts.jsonl 也没有 flow_cache.json。\n"
            "先跑：python _dump_my_texts.py    （全量导出，推荐）")
    with open(path, encoding="utf-8") as f:
        cache = json.load(f)
    out = []
    for wxid, chat in (cache.get("chats") or {}).items():
        for m in (chat.get("msgs") or []):
            if m.get("dir") == "out" and m.get("kind") == "text" and m.get("text"):
                out.append({"chat": chat.get("name") or wxid, "wxid": wxid,
                            "t": m.get("t", 0), "text": m["text"].strip()})
    return out


def attach_names(msgs: list) -> list:
    """把 wxid 换成会话显示名（账本里只存了 wxid）。"""
    try:
        import wcstat
        names = wcstat.session_names(wcstat.find_db_dir())
    except Exception:
        names = {}
    if not names:
        # 退回缓存里的名字
        try:
            with open(os.path.join(HERE, "flow_cache.json"), encoding="utf-8") as f:
                cache = json.load(f)
            names = {w: (c.get("name") or w) for w, c in (cache.get("chats") or {}).items()}
        except Exception:
            names = {}
    for m in msgs:
        m["chat"] = names.get(m.get("wxid")) or m.get("chat") or "?"
    return msgs


def analyze(msgs: list, top: int = 40) -> dict:
    """统计风格特征。全部是算出来的事实，不做主观判断。"""
    texts = [m["text"] for m in msgs]
    if not texts:
        raise SystemExit("没有找到「我发出的」文本消息，无法分析")

    lens = [len(t) for t in texts]
    lens_sorted = sorted(lens)
    def pct(p):
        return lens_sorted[min(len(lens_sorted) - 1, int(len(lens_sorted) * p))]

    # 标点与句尾习惯
    no_end_punct = sum(1 for t in texts if t and t[-1] not in "。！？!?.…~～")
    with_ellipsis = sum(1 for t in texts if "。。" in t or "..." in t or "…" in t)
    with_wave = sum(1 for t in texts if "~" in t or "～" in t)
    with_q = sum(1 for t in texts if "?" in t or "？" in t)
    with_excl = sum(1 for t in texts if "!" in t or "！" in t)
    laugh_n = sum(1 for t in texts if LAUGH.search(t))
    emoji_n = sum(1 for t in texts if re.search(
        r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", t))
    laugh_only = sum(1 for t in texts if LAUGH.fullmatch(t.strip()))
    short_ack = sum(1 for t in texts if len(t) <= 2)

    # 分词：中文按 2-4 字 n-gram 粗切，再排掉停用词
    gram = Counter()
    for t in texts:
        clean = re.sub(r"[^\u4e00-\u9fff]", "", t)
        for n in (2, 3, 4):
            for i in range(len(clean) - n + 1):
                g = clean[i:i + n]
                if g in STOP:
                    continue
                gram[g] += 1
    # 按频次从高到低筛，把「已被更长高频词包含」的短词丢掉。
    # 例如 "不赖" 上榜后，"不赖不"、"不赖不赖" 就应该被吸收掉。
    # 注意包含关系的方向：要判断「已入选的短词 p 是否被候选 g 包含」= p in g，
    # 写成 g in p 会反过来，去不掉子串（踩过这个坑）。
    phrases = []
    candidates = [(g, c) for g, c in gram.most_common(top * 8) if c >= 3]
    for g, c in candidates:
        if any(g == p for p, _ in phrases):
            continue
        if any(p in g for p, _ in phrases):
            continue          # 候选里包含了已入选的词 → 是它的超串变体，跳过
        phrases.append((g, c))
        if len(phrases) >= top:
            break

    particles = Counter()
    for t in texts:
        for ch in t:
            if ch in PARTICLES:
                particles[ch] += 1

    punct = Counter()
    for t in texts:
        for ch in t:
            if ch in "，。！？、；：…~～.,!?":
                punct[ch] += 1

    # 按会话分组，看对不同人的语气差异
    per_chat = {}
    for m in msgs:
        d = per_chat.setdefault(m["chat"], {"n": 0, "len": [], "laugh": 0, "samples": []})
        d["n"] += 1
        d["len"].append(len(m["text"]))
        if LAUGH.search(m["text"]):
            d["laugh"] += 1
        if len(d["samples"]) < 3 and 2 <= len(m["text"]) <= 40:
            d["samples"].append(m["text"])

    chats = []
    for name, d in per_chat.items():
        if d["n"] < 5:
            continue
        chats.append({
            "name": name, "n": d["n"],
            "avg_len": round(statistics.mean(d["len"]), 1),
            "laugh_ratio": round(d["laugh"] / d["n"], 2),
            "samples": d["samples"],
        })
    chats.sort(key=lambda x: -x["n"])

    overall_avg = round(statistics.mean(lens), 1)
    for c in chats:
        # 相对整体判断语气偏"热络"还是"简短"
        c["tone"] = ("热络" if c["laugh_ratio"] > 0.12 and c["avg_len"] >= overall_avg
                     else "简短" if c["avg_len"] < overall_avg * 0.7
                     else "正常")

    return {
        "total": len(texts),
        "avg_len": overall_avg,
        "median_len": statistics.median(lens),
        "p10": pct(0.10), "p90": pct(0.90),
        "short_ratio": round(short_ack / len(texts), 3),
        "no_end_punct_ratio": round(no_end_punct / len(texts), 3),
        "ellipsis_ratio": round(with_ellipsis / len(texts), 3),
        "wave_ratio": round(with_wave / len(texts), 3),
        "question_ratio": round(with_q / len(texts), 3),
        "excl_ratio": round(with_excl / len(texts), 3),
        "laugh_ratio": round(laugh_n / len(texts), 3),
        "laugh_only_ratio": round(laugh_only / len(texts), 3),
        "emoji_ratio": round(emoji_n / len(texts), 3),
        "phrases": phrases[:top],
        "particles": particles.most_common(12),
        "punct": punct.most_common(10),
        "chats": chats[:20],
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_persona(st: dict) -> str:
    """把统计渲染成给人看 + 给模型看的档案。"""
    L = []
    A = L.append
    A("# 我的说话风格档案")
    A("")
    A(f"> 从 **{st['total']}** 条我发出的真实消息里统计出来的，生成于 {st['generated']}。")
    A(">")
    A("> 这个文件可以**随手改**——改完重新生成拟回复就会按新的来。")
    A("> 客观统计部分我建议保留（那是事实），写作规则部分你想怎么改都行。")
    A("")
    A("## 一、客观统计")
    A("")
    A("| 指标 | 数值 | 说明 |")
    A("|---|---|---|")
    A(f"| 平均长度 | **{st['avg_len']} 字** | 中位数 {st['median_len']} 字，"
      f"10% 分位 {st['p10']} 字，90% 分位 {st['p90']} 字 |")
    A(f"| 极短回复（≤2字）占比 | {st['short_ratio']*100:.1f}% | "
      f"「嗯」「好」「？」这类 |")
    A(f"| 句尾不加标点 | {st['no_end_punct_ratio']*100:.1f}% | "
      f"我基本不用句号收尾 |")
    A(f"| 用「~」 | {st['wave_ratio']*100:.1f}% | 波浪号是软化语气用的 |")
    A(f"| 用「。。」或省略号 | {st['ellipsis_ratio']*100:.1f}% | 无语/无奈时用 |")
    A(f"| 问句 | {st['question_ratio']*100:.1f}% | |")
    A(f"| 感叹句 | {st['excl_ratio']*100:.1f}% | |")
    A(f"| 带笑（哈哈/嘿嘿/😂） | {st['laugh_ratio']*100:.1f}% | "
      f"其中纯笑（只有哈哈）占 {st['laugh_only_ratio']*100:.1f}% |")
    A(f"| 带 emoji | {st['emoji_ratio']*100:.1f}% | |")
    A("")
    A("## 二、口头禅与高频表达")
    A("")
    if st["phrases"]:
        A("出现次数（这些是从我的消息里数出来的，不是我编的）：")
        A("")
        A("```")
        for g, c in st["phrases"]:
            A(f"{g}   ×{c}")
        A("```")
    if st["particles"]:
        A("")
        A("语气助词使用频次：" + "、".join(f"{c}×{n}" for c, n in st["particles"]))
    if st["punct"]:
        A("")
        A("标点使用频次：" + "、".join(f"{c}×{n}" for c, n in st["punct"]))
    A("")
    A("## 三、对不同人的语气差异")
    A("")
    A("| 会话 | 消息数 | 平均长度 | 带笑比例 | 语气 |")
    A("|---|---|---|---|---|")
    for c in st["chats"]:
        A(f"| {c['name'][:18]} | {c['n']} | {c['avg_len']} 字 | "
          f"{c['laugh_ratio']*100:.0f}% | {c['tone']} |")
    A("")
    A("## 四、真实样本（原文，给我自己核对）")
    A("")
    for c in st["chats"][:8]:
        if not c["samples"]:
            continue
        A(f"**{c['name'][:20]}**")
        for s in c["samples"]:
            A(f"- {s}")
        A("")
    A("## 五、写作规则（给模型看的，可以随便改）")
    A("")
    A(f"1. **长度**：平均 {st['avg_len']} 字。多数情况就一到两句，"
      f"不要写成一段话；{st['short_ratio']*100:.0f}% 的时候我本来就是极短回复。")
    A("2. **标点**："
      + ("我基本不拿句号收尾，句子之间用逗号或者直接空格。"
         if st["no_end_punct_ratio"] > 0.9 else
         "标点用得比较正常，但也不要太正式。"))
    if st["wave_ratio"] > 0.05:
        A("3. **波浪号**：我常用「~」来软化语气，可以适当带一个，但别每句都带。")
    if st["laugh_ratio"] > 0.1:
        A("4. **笑**：我经常用「哈哈」「嘿嘿」这类，"
          f"纯笑（只有哈哈两个字）也常见，这是自然的不是敷衍。")
    else:
        A("4. **笑**：我笑的时候不多，别硬加。")
    if st["emoji_ratio"] > 0.08:
        A("5. **emoji**：我会用，但克制，通常一句最多一个。")
    else:
        A("5. **emoji**：我很少用，除非对方先用。")
    A("6. **称呼**：看关系。对群里/不熟的人更简短客气，对朋友直接。")
    A("7. **禁止**：不要用「亲」「呢呢」「哦哦哦」这种假热情；"
      "不要解释自己在帮忙；不要加「希望这对你有帮助」这类客服腔。")
    A("8. **要像人**：允许有语气词、允许不完整、允许反问。"
      "宁可短，不要凑字数。")
    A("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40, help="口头禅取前 N 个")
    args = ap.parse_args()

    msgs = attach_names(_msgs_of_mine())
    print(f"读到我说过的消息 {len(msgs)} 条")
    st = analyze(msgs, top=args.top)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    with open(PERSONA_FILE, "w", encoding="utf-8") as f:
        f.write(render_persona(st))
    print(f"✓ 风格档案 -> {PERSONA_FILE}")
    print(f"✓ 原始统计 -> {STATS_FILE}")
    print()
    print(f"平均长度 {st['avg_len']} 字 / 中位数 {st['median_len']} 字")
    print(f"极短回复 {st['short_ratio']*100:.0f}% / 不带句尾标点 {st['no_end_punct_ratio']*100:.0f}%"
          f" / 带笑 {st['laugh_ratio']*100:.0f}% / 带emoji {st['emoji_ratio']*100:.0f}%")
    print("口头禅 top10:", "、".join(f"{g}({c})" for g, c in st["phrases"][:10]))


if __name__ == "__main__":
    main()
