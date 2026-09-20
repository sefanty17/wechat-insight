#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wccount.py — 微信聊天字数统计核心（分类计数）

把一条消息拆成 4 类分别计数，互不重叠：
  cjk   : 中日韩文字（汉字/假名/谚文），按「字」计
  word  : 英文单词（一串拉丁字母算 1）
  digit : 数字串（一串阿拉伯数字算 1）
  emoji : 表情（含 ZWJ 组合、肤色修饰、旗帜、keycap、❤️ 之类按 1 个算）
  punct : 标点符号（单独记，不计入 total，方便你自己决定要不要算进去）

total = cjk + word + digit + emoji   （不含标点、不含空白）

设计原则：emoji 先剥离，再从剩余文本里数字符/词，保证不会重复计数。
"""

from __future__ import annotations

import json
import re
import unicodedata

# --------------------------------------------------------------------------
# 正则片段
# --------------------------------------------------------------------------

# 中日韩文字：汉字（含扩展 A/B+ 代理对）、假名、谚文、CJK 兼容表意文字
_CJK = (
    r"\u3400-\u4dbf"          # 扩展 A
    r"\u4e00-\u9fff"          # 基本区
    r"\uf900-\ufaff"          # 兼容表意
    r"\u3040-\u309f"          # 平假名
    r"\u30a0-\u30ff"          # 片假名
    r"\uac00-\ud7af"          # 谚文音节
)

# 表情/符号基元
_EMOJI_BASE = (
    r"\u2190-\u21ff"          # 箭头
    r"\u2300-\u23ff"          # 技术符号
    r"\u2460-\u24ff"          # 带圈字符
    r"\u25a0-\u27bf"          # 几何图形 + 装饰符号(含 ✨✅ 等)
    r"\u2b00-\u2bff"          # 杂项符号箭头
    r"\u3030\u303d\u3297\u3299"
    r"\u00a9\u00ae\u203c\u2049\u2122\u2139"
    r"\u2194-\u21aa"
    r"\u231a-\u231b\u2328\u23cf\u23e9-\u23fa"
    r"\u24c2"
    r"\u25aa-\u25fe"
    r"\u2600-\u27ef"
    r"\u2934-\u2935"
    r"\u2b05-\u2b07\u2b1b-\u2b1c\u2b50\u2b55"
    r"\u3030\u303d"
    r"\u3297\u3299"
    r"\U0001f000-\U0001faff"  # 麻将/扑克/交通/表情/补充符号
    r"\U0001f1e6-\U0001f1ff"  # 区域指示符(国旗)
)

# 一个 emoji = 基元 + (变体选择符|肤色|键帽) *  + (ZWJ + 基元...)*
_EMOJI_RE = re.compile(
    rf"(?:[{_EMOJI_BASE}]"
    rf"[\ufe0e\ufe0f\u20e3\U0001f3fb-\U0001f3ff]*"
    rf"(?:\u200d[{_EMOJI_BASE}][\ufe0e\ufe0f\u20e3\U0001f3fb-\U0001f3ff]*)*)"
)

# keycap（1️⃣ #️⃣ *️⃣）必须以 U+20E3 结尾才算表情，
# 否则普通的 "1#" 会被误判成表情
_KEYCAP_RE = re.compile(r"(?<![0-9#*])([0-9#*]\ufe0f?\u20e3)")

# 英文单词（拉丁字母串，允许内部撇号/连字符，如 don't / e-mail）
_WORD_RE = re.compile(r"[A-Za-z]+(?:['\u2019\-][A-Za-z]+)*")

# 数字串（允许千分位逗号与小数点，如 1,234.56）
_NUM_RE = re.compile(r"\d+(?:[,.]\d+)*")

# 标点：Unicode 分类 P* 与 S* 之外的符号统一在代码里用 unicodedata 判定
_WS_RE = re.compile(r"\s+")


def count_text(text: str) -> dict:
    """对单条文本做分类计数，返回各类数量。

    >>> count_text("你好hello世界123世界😀")["total"]
    9
    """
    if not text or not isinstance(text, str):
        return _zero()

    cjk = word = digit = emoji = punct = 0

    # 1) 先摘掉 keycap 表情（1️⃣ #️⃣ *️⃣），必须在数字匹配之前做，
    #    否则会被拆成「数字 + 符号」
    rest_parts: list[str] = []
    pos = 0
    for m in _KEYCAP_RE.finditer(text):
        rest_parts.append(text[pos:m.start()])
        emoji += 1
        pos = m.end()
    rest_parts.append(text[pos:])
    text = "".join(rest_parts)

    # 2) 再摘掉普通 emoji，避免其中的数字/符号被重复统计
    rest_parts = []
    pos = 0
    for m in _EMOJI_RE.finditer(text):
        rest_parts.append(text[pos:m.start()])
        emoji += 1
        pos = m.end()
    rest_parts.append(text[pos:])
    rest = "".join(rest_parts)

    # 3) 英文单词与数字串，从剩余文本里摘掉
    def _strip(pattern: re.Pattern, counter_name: str) -> None:
        nonlocal rest, word, digit
        def repl(_m: re.Match) -> str:
            nonlocal word, digit
            if counter_name == "word":
                word += 1
            else:
                digit += 1
            return "\u0000"  # 占位符，保持长度无关紧要，但避免粘连
        rest = pattern.sub(repl, rest)

    _strip(_WORD_RE, "word")
    _strip(_NUM_RE, "digit")

    # 4) 剩余字符：汉字计数，标点/其他符号计数
    for ch in rest:
        if ch == "\u0000" or ch.isspace():
            continue
        cp = ord(ch)
        # 汉字/假名/谚文
        if (
            0x3400 <= cp <= 0x4DBF
            or 0x4E00 <= cp <= 0x9FFF
            or 0xF900 <= cp <= 0xFAFF
            or 0x3040 <= cp <= 0x30FF
            or 0xAC00 <= cp <= 0xD7AF
            or 0x20000 <= cp <= 0x3FFFF  # 扩展 B 及以上
        ):
            cjk += 1
            continue
        # 标点与符号（emoji 已剥离，剩下的符号多为标点）
        if unicodedata.category(ch)[0] in ("P", "S"):
            punct += 1
        else:
            # 其它可打印字符（如注音、泰文、俄文等）算作 1 个字
            cjk += 1

    total = cjk + word + digit + emoji
    return {
        "cjk": cjk,
        "word": word,
        "digit": digit,
        "emoji": emoji,
        "punct": punct,
        "total": total,
        "chars": len(text),
    }


def _zero() -> dict:
    return {"cjk": 0, "word": 0, "digit": 0, "emoji": 0, "punct": 0, "total": 0, "chars": 0}


def add_counts(a: dict, b: dict) -> dict:
    """把 b 累加进 a，返回新字典。"""
    out = dict(a)
    for k in ("cjk", "word", "digit", "emoji", "punct", "total", "chars", "msgs"):
        if k in b:
            out[k] = out.get(k, 0) + b[k]
    return out


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------

def _selftest() -> int:
    cases = [
        ("你好世界", {"cjk": 4, "total": 4}),
        ("hello world", {"word": 2, "total": 2}),
        ("你好hello世界", {"cjk": 4, "word": 1, "total": 5}),
        ("12345", {"digit": 1, "total": 1}),
        ("abc123def", {"word": 2, "digit": 1, "total": 3}),
        ("😀", {"emoji": 1, "total": 1}),
        ("😀😀😀", {"emoji": 3, "total": 3}),
        ("👍🏻", {"emoji": 1, "total": 1}),          # 肤色修饰
        ("👨‍👩‍👧‍👦", {"emoji": 1, "total": 1}),  # ZWJ 家庭
        ("🇨🇳", {"emoji": 2, "total": 2}),          # 国旗 = 2 区域指示符
        ("❤️", {"emoji": 1, "total": 1}),           # 心+VS16
        ("1️⃣", {"emoji": 1, "total": 1}),           # keycap
        ("你好，世界！", {"cjk": 4, "punct": 2, "total": 4}),
        ("hello, world!", {"word": 2, "punct": 2, "total": 2}),
        ("  ", {"total": 0}),
        ("", {"total": 0}),
        ("don't", {"word": 1, "total": 1}),
        ("e-mail地址test", {"word": 2, "cjk": 2, "total": 4}),
        ("价格1,234.56元", {"cjk": 3, "digit": 1, "total": 4}),
    ]
    bad = 0
    for text, expect in cases:
        got = count_text(text)
        for k, v in expect.items():
            if got[k] != v:
                print(f"FAIL {text!r}: {k} 期望 {v} 实得 {got[k]}  | {got}")
                bad += 1
    if bad == 0:
        print(f"OK 自测全部通过（{len(cases)} 例）")
        # 展示几个丰富例子
        for t in ["今天开会，说了 3 个要点 abc def 😀😀！", "哈哈哈哈哈哈哈", "OK👌收到，明天见~"]:
            print(f"  {t!r} -> {json.dumps(count_text(t), ensure_ascii=False)}")
    return bad


if __name__ == "__main__":
    import sys

    raise SystemExit(1 if _selftest() else 0)
