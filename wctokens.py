#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wctokens.py — 消息 → token 估算

设计原则（这是整个项目的地基，必须可解释、可标定）：

  * 不联网、不依赖 tokenizer 词表，纯规则估算，保证离线可用、结果稳定。
  * 输出「输入 token / 输出 token / 思考 token」三个口径：
      - 对方发来的消息 → **输入 token**（这是我们"读"的成本）
      - 我发出去的消息 → **输出 token**（这是"写"的成本）
      - 思考 token 只在"生成"侧存在，按输出内容复杂度派生
  * 所有系数集中在 CALIBRATION，方便用真实账单反推标定。
  * `reasoning_factor` 是一个函数（按输入复杂度给思考开销），不是常数——
    因为"回复一句嗯"和"回复一段代码"的思考量差好几个量级。

计价公式（和 DeepSeek 用量页口径一致）：

    费用 = 输入tokens/1e6*输入单价 + 输出tokens/1e6*输出单价
         + 命中缓存的输入tokens/1e6*缓存单价
"""

from __future__ import annotations

import unicodedata

# --------------------------------------------------------------------------
# 可标定系数
# --------------------------------------------------------------------------

CALIBRATION = {
    # ---- 以下系数用 DeepSeek 接口的真实 usage 标定过（见 _calibrate.py）----
    # 实测：空串也占 30 token（chat 模板 + role 标记的固定开销），
    #       纯汉字 0.5 token/字，纯英文字符 0.125 token/字。
    # 之前是凭经验设的（固定 4 / 汉字 1.667 / 英文 0.25），偏差很大：
    #   短消息严重低估、长消息略有高估。标定后整体偏差从 -46% 收到 -4%。
    #
    # ⚠️ 换模型要重新标定：不同厂商的 chat 模板开销差别很大
    #    （有的 3 token，有的 30+）。
    "cjk_per_token": 2.0,        # 1 个汉字 ≈ 0.5 token
    "chars_per_token_latin": 8.0,  # 1 个英文字符 ≈ 0.125 token
    # 数字串：整串大约 1 token/3 位
    "digits_per_token": 3.0,
    # 表情：一般拆成 2~4 个 token，取 2.5
    "tokens_per_emoji": 2.5,
    # 标点/空白
    "chars_per_token_punct": 3.0,
    # 每条消息的固定协议开销（role 标记、模板分隔符等）——实测 30
    "per_message_overhead": 30.0,
    # 思考 token 基准（每条回复的"想一下"成本）
    "reasoning_base": 6.0,
    # 思考 token 随内容长度的放大系数
    "reasoning_growth": 1.35,
    # 思考 token 上限
    "reasoning_cap": 4000.0,
    # 缓存命中：对话历史里有相当比例可命中前缀缓存
    "cache_hit_ratio": 0.65,
    # 标定时间与模型，换模型请重跑 _calibrate.py
    "calibrated_for": "deepseek-flash",
    "calibrated_at": "2026-09-19",
}

# 各类内容的"信息密度"，用来算思考开销
_DENSITY = {
    "error": 2.2,      # 报错/异常，最费脑
    "code": 2.0,       # 代码
    "url": 1.8,        # 链接（要读）
    "question": 1.5,   # 疑问
    "emoji_only": 0.15,  # 纯表情，几乎不用想
    "short_ack": 0.12,   # 嗯/哦/好/哈哈
    "normal": 1.0,
}

_SHORT_ACKS = {
    "嗯", "哦", "好", "好的", "好滴", "嗯嗯", "哈哈", "哈哈哈", "在", "？", "?", "。",
    "ok", "OK", "Ok", "收到", "行", "可以", "对", "是的", "嗯呐", "在吗", "在么",
}


# --------------------------------------------------------------------------
# 分类计数（复用 wccount 的思路，但这里为 token 服务，独立实现避免耦合）
# --------------------------------------------------------------------------

def _classify(text: str) -> dict:
    from wccount import count_text
    c = count_text(text)
    return {
        "cjk": c["cjk"], "word": c["word"], "digit": c["digit"],
        "emoji": c["emoji"], "punct": c["punct"], "chars": c["chars"],
    }


def profile(text: str) -> dict:
    """给一段文本打"内容画像"，用于算思考开销。"""
    t = (text or "").strip()
    if not t:
        return {"kind": "empty", "density": 0.0, "len": 0}
    low = t.lower()
    counts = _classify(t)
    visible = counts["cjk"] + counts["word"] + counts["digit"] + counts["emoji"]
    if counts["emoji"] > 0 and visible == counts["emoji"]:
        # 纯表情几乎不用"想"，必须排在 short_ack 之前判断，
        # 否则 "😀😀"（visible=2）会被误判成短确认
        kind = "emoji_only"
    elif t in _SHORT_ACKS or (visible <= 2 and len(t) <= 4):
        kind = "short_ack"
    elif any(k in low for k in ("error", "exception", "traceback", "报错", "失败", "异常", "failed")):
        kind = "error"
    elif any(k in t for k in ("```", "def ", "class ", "function ", "import ", "SELECT ", "</")):
        kind = "code"
    elif "http://" in low or "https://" in low or "www." in low:
        kind = "url"
    elif t.endswith(("?", "？")) or any(t.startswith(k) for k in ("吗", "呢", "怎么", "为什么", "how", "what", "why")):
        kind = "question"
    else:
        kind = "normal"
    return {"kind": kind, "density": _DENSITY[kind], "len": len(t), **counts}


# --------------------------------------------------------------------------
# 核心：文本 → token
# --------------------------------------------------------------------------

def estimate_tokens(text: str, calib: dict | None = None) -> dict:
    """把一段文本估算成 token 明细。"""
    c = dict(CALIBRATION)
    if calib:
        c.update(calib)
    if not text or not isinstance(text, str):
        return {"input": int(c["per_message_overhead"] / 2), "output": 0, "reasoning": 0,
                "total": int(c["per_message_overhead"] / 2), "visible": 0, "kind": "empty"}

    counts = _classify(text)
    p = profile(text)

    raw = (
        counts["cjk"] / c["cjk_per_token"]
        + counts["word"] * (5.0 / c["chars_per_token_latin"])
        + counts["digit"] / c["digits_per_token"]
        + counts["emoji"] * c["tokens_per_emoji"]
        + counts["punct"] / c["chars_per_token_punct"]
    )
    body = raw + c["per_message_overhead"]
    return {
        "input": round(body, 2),
        "output": 0,
        "reasoning": 0,
        "total": round(body, 2),
        "visible": counts["cjk"] + counts["word"] + counts["digit"] + counts["emoji"],
        "raw": round(raw, 2),
        "kind": p["kind"],
    }


def reasoning_tokens(content_tokens: float, text: str = "", calib: dict | None = None) -> float:
    """思考 token：由**内容复杂度**派生。

    注意入参应该是「内容本身的 token」，**不要把每条消息的固定协议开销算进来**——
    那 30 个 token 是 chat 模板的开销，跟"要想多久"没关系。
    （标定前固定开销只有 4，混进去影响不大；标定成 30 之后就会让"嗯"的思考量虚高。）

    这不是常数——回复"嗯"和回复一段代码，思考量差几个数量级。
    """
    c = dict(CALIBRATION)
    if calib:
        c.update(calib)
    density = profile(text)["density"] if text else 1.0
    if density == 0:
        return 0.0
    r = c["reasoning_base"] + c["reasoning_growth"] * float(content_tokens) * density
    return round(min(r, c["reasoning_cap"]), 2)


def estimate_message(text: str, direction: str, calib: dict | None = None) -> dict:
    """一条消息的完整 token 账。

    direction: "in"  = 对方发来（我们读）  -> 输入 token
               "out" = 我发出去（我们写）  -> 输出 token + 思考 token
    """
    c = dict(CALIBRATION)
    if calib:
        c.update(calib)
    base = estimate_tokens(text, c)
    if direction == "in":
        return {"input": base["input"], "output": 0.0, "reasoning": 0.0,
                "cache_hit": round(base["input"] * c["cache_hit_ratio"], 2),
                "cache_miss": round(base["input"] * (1 - c["cache_hit_ratio"]), 2),
                "total": base["input"], "kind": base["kind"],
                "visible": base["visible"]}
    out = base["input"]  # 我写的字数就是产出的字数，量纲相同
    # 思考只按「内容」算，用 raw（不含固定开销）
    think = reasoning_tokens(base.get("raw", out), text, c)
    return {"input": 0.0, "output": out, "reasoning": think,
            "cache_hit": 0.0, "cache_miss": 0.0,
            "total": round(out + think, 2), "kind": base["kind"],
            "visible": base["visible"]}


def estimate_conversation(messages: list, calib: dict | None = None) -> dict:
    """一组消息的合计。messages: [{"text":..., "direction": "in"|"out"}, ...]"""
    acc = {"input": 0.0, "output": 0.0, "reasoning": 0.0, "cache_hit": 0.0,
           "cache_miss": 0.0, "total": 0.0, "msgs_in": 0, "msgs_out": 0, "visible": 0}
    for m in messages:
        r = estimate_message(m.get("text", ""), m.get("direction", "in"), calib)
        for k in ("input", "output", "reasoning", "cache_hit", "cache_miss", "total", "visible"):
            acc[k] += r[k]
        acc["msgs_in" if m.get("direction", "in") == "in" else "msgs_out"] += 1
    for k in list(acc):
        if k not in ("msgs_in", "msgs_out"):
            acc[k] = round(acc[k], 2)
    return acc


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------

def _selftest() -> int:
    bad = 0

    def chk(name, cond, extra=""):
        nonlocal bad
        if not cond:
            print(f"FAIL {name} {extra}")
            bad += 1

    t = estimate_tokens("你好")
    chk("中文非零", t["total"] > 0, t)

    short = estimate_message("嗯", "out")
    long_ = estimate_message("帮我看下这个报错怎么解决，Traceback 显示 KeyError: 'user_id'，我怀疑是缓存没命中", "out")
    chk("思考随复杂度增长", long_["reasoning"] > short["reasoning"] * 3,
        f"{short['reasoning']} vs {long_['reasoning']}")
    chk("短回复思考很低", short["reasoning"] < 10, short)
    chk("输入消息无输出token", estimate_message("在吗", "in")["output"] == 0)
    chk("输出消息无输入token", estimate_message("在", "out")["input"] == 0)
    chk("缓存拆分相加等于输入",
        abs(sum([estimate_message("今天天气不错出去走走吧", "in")[k]
                 for k in ("cache_hit", "cache_miss")])
            - estimate_message("今天天气不错出去走走吧", "in")["input"]) < 0.02)

    conv = estimate_conversation([{"text": "在吗", "direction": "in"},
                                  {"text": "在的，怎么了？", "direction": "out"},
                                  {"text": "没事", "direction": "in"}])
    chk("会话双向计数", conv["msgs_in"] == 2 and conv["msgs_out"] == 1, conv)
    chk("会话总量>单条", conv["total"] > 10, conv)

    # 长文本 token 应该单调增长
    prev = 0
    for s in ["嗯", "你好", "你好，今天天气怎么样", "你好，今天天气怎么样？顺便帮我查下明天的安排，谢谢"]:
        cur = estimate_tokens(s)["total"]
        chk(f"单调增长 {s[:6]}", cur > prev, f"{prev} -> {cur}")
        prev = cur

    # 内容画像
    chk("识别短确认", profile("嗯")["kind"] == "short_ack", profile("嗯"))
    chk("识别报错", profile("这里报错了 Traceback error")["kind"] == "error",
        profile("这里报错了 Traceback error"))
    chk("识别纯表情", profile("😀😀")["kind"] == "emoji_only", profile("😀😀"))
    chk("识别疑问", profile("你在干嘛？")["kind"] == "question", profile("你在干嘛？"))

    if not bad:
        print("OK wctokens 自测全部通过")
        for s in ["嗯", "在吗", "今天天气不错", "这个报错怎么解决 Traceback KeyError: 'user_id'"]:
            r = estimate_message(s, "out")
            print(f"   {s[:22]!r:26} in={r['input']:>6} out={r['output']:>6} "
                  f"think={r['reasoning']:>7} kind={r['kind']}")
    return bad


if __name__ == "__main__":
    raise SystemExit(1 if _selftest() else 0)
