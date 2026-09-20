#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wcmsg.py — 把微信原始消息解码成「能显示、能计费」的东西

微信 4.x 的 message_content 分三种形态：
  1. 纯文本（local_type=1）——直接用
  2. zstd 压缩的 blob（图片/表情/语音等，magic = 28 b5 2f fd）——要先解压
  3. XML/JSON 文本（引用、链接、转账等）——抽关键字段

解码后统一产出：
    {"kind": ..., "text": 显示文本, "meta": {...}, "countable": bool}

其中 text 既用于聊天界面显示，也用于 token 估算。
countable=False 的类型（如系统消息）默认不进账单。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

try:
    from compression import zstd as _zstd  # Python 3.14+
except Exception:  # pragma: no cover
    try:
        import zstandard as _zstd  # type: ignore
    except Exception:
        _zstd = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# 消息类型 -> 语义
TYPE_MAP = {
    1: ("text", "文本"),
    3: ("image", "图片"),
    34: ("voice", "语音"),
    37: ("friend_request", "好友申请"),
    42: ("card", "名片"),
    43: ("video", "视频"),
    47: ("sticker", "表情"),
    48: ("location", "位置"),
    49: ("app", "链接/文件"),
    50: ("voip", "通话"),
    10000: ("system", "系统"),
    10002: ("revoke", "撤回"),
}

# 4.x 里带「子类型」的复合 local_type：高位是标记，低位才是真类型。
# 实测 244813135921 的内部 XML 是 <type>57</type>，即「引用回复」。
# 这里把已知的复合值直接映射成语义类型。
COMPOSITE_MAP = {
    244813135921: "quote",
    266287972401: "quote",
    17179869233: "app",
    21474836529: "app",
}


def decompress(data: bytes) -> bytes:
    """尽力解压 zstd 数据；失败就原样返回（绝不抛异常打断统计）。"""
    if not data:
        return b""
    if _zstd is None:
        return data
    if not data.startswith(ZSTD_MAGIC):
        return data
    try:
        if hasattr(_zstd, "decompress"):
            return _zstd.decompress(data)
        d = _zstd.ZstdDecompressor()  # type: ignore
        return d.decompress(data)
    except Exception:
        return data


def _as_text(raw) -> str:
    """把原始 content 转成可读字符串。

    坑：别写 `b[:1] in (b"{", b"[", b"<")` —— 左边是长度 1 的 bytes，
    右边是多字节字面量，长度不等永不相等，判断恒为假。要比就比整数。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        b = decompress(bytes(raw))
        # 解压后可能仍是二进制（缩略图等），只有像文本时才转字符串
        if b[:5] == b"<?xml" or (b[:1] and b[0] in (0x7B, 0x5B, 0x3C)):  # { [ <
            b = b.split(b"\x00")[0]
            for enc in ("utf-8", "gbk"):
                try:
                    return b.decode(enc)
                except UnicodeDecodeError:
                    continue
            return b.decode("utf-8", errors="ignore")
        return ""
    return str(raw)


def _attr(attrs: str, name: str) -> str:
    """从属性串里取一个属性值。

    边界处理是这里最容易错的地方，两个坑都踩过了：
      1. 不能用 `\\b`——属性名后面紧跟 `=`，`name\\b` 在 `name=` 处不成立。
      2. 不能只用 `(?:^|\\s)` 开头——`tousername="..."` 里含有 `name=`，
         会被当成 `name` 属性匹配到。实测就是这里把 md5 挡住了，
         导致所有表情都读不出名字。
    做法：把属性串前后补空格，然后要求「空格 + 属性名 + 空格 + =」。
    """
    hay = " " + attrs + " "
    m = re.search(rf"\s{re.escape(name)}\s*=\s*[\"']([^\"']*)[\"']", hay)
    return m.group(1).strip() if m else ""


def _xml_attr(s: str, name: str) -> str:
    """在整个 XML 里全局找一个属性值，不关心它挂在哪个标签上。

    `_xml_field` 是按标签名找的，只适合 `<md5>值</md5>` 这种成对写法。
    而微信大量使用 `<emoji md5="..." productid="...">` 这种「属性即字段」
    的风格——字段名根本不是标签名。所以必须有这条全局属性通道。
    """
    if not s:
        return ""
    hay = " " + s.replace(">", "> ") + " "
    m = re.search(rf"\s{re.escape(name)}\s*=\s*[\"']([^\"']*)[\"']", hay)
    return m.group(1).strip() if m else ""


def _xml_field(s: str, tag: str) -> str:
    """从 XML 里取字段值。

    顺序很重要：**先看成对标签，再退回属性**。
    `<(tag)>(.*?)</\\1>` 如果先走且用贪婪跨度，会把整个 XML 当成"值"；
    所以成对分支要求值里不含标签。
    """
    if not s:
        return ""
    # 1) 成对写法：<tag>纯文本值</tag>（值里不含标签）
    m = re.search(rf"<{tag}>\s*([^<>]*?)\s*</{tag}>", s, re.S)
    if m:
        v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S)
        return v.strip()
    # 2) 属性写法：<tag ...> 或 <tag ... />，优先同名属性
    m = re.search(rf"<{tag}(?:\s[^>]*)?/?>", s, re.S)
    if m:
        for name in ("value", "name", "alias", tag):
            v = _attr(m.group(0), name)
            if v:
                return v
    # 3) 兜底：全局找同名属性
    return _xml_attr(s, tag)


def _looks_like_xml(s: str) -> bool:
    return s.lstrip().startswith("<")


def _looks_base64(v: str) -> bool:
    """判断一个字段值是不是 base64 编码的字节串（这种值不适合当名字显示）。"""
    if len(v) < 12 or len(v) % 4 != 0:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", v):
        return False
    # 有 padding 基本可以确定是 base64
    return v.endswith("=") or bool(re.search(r"[+/]", v))


def decode(local_type: int, content, *, max_len: int = 4000) -> dict:
    """解码一条消息。返回 dict(kind, label, text, meta, countable)。"""
    spec = TYPE_MAP.get(local_type)
    if spec is None and local_type in COMPOSITE_MAP:
        spec = (COMPOSITE_MAP[local_type], "引用")
    kind, label = spec if spec else ("raw", f"类型{local_type}")

    # 未知类型：可能仍是文本
    if spec is None:
        s = _as_text(content)
        if s and not _looks_like_xml(s):
            return {"kind": "text", "label": "文本", "text": s[:max_len],
                    "meta": {}, "countable": True}
        return {"kind": "unknown", "label": label, "text": "", "meta": {}, "countable": False}

    if kind == "text":
        return {"kind": "text", "label": "文本", "text": _as_text(content)[:max_len],
                "meta": {}, "countable": True}

    if kind == "image":
        # 缩略图（type=3 且压缩过）走不了文本，只给占位
        return {"kind": "image", "label": "图片", "text": "[图片]",
                "meta": {"w": None, "h": None}, "countable": True}

    if kind == "voice":
        s = _as_text(content)
        secs = _xml_field(s, "voicelength")
        try:
            secs = int(int(secs) / 1000)
        except Exception:
            secs = None
        return {"kind": "voice", "label": "语音", "text": f"[语音{secs}″]" if secs else "[语音]",
                "meta": {"seconds": secs}, "countable": True}

    if kind == "video":
        s = _as_text(content)
        return {"kind": "video", "label": "视频", "text": "[视频]",
                "meta": {"playlength": _xml_field(s, "playlength")}, "countable": True}

    if kind == "sticker":
        s = _as_text(content)
        candidates = [
            _xml_field(s, "name"), _xml_attr(s, "alias"), _xml_attr(s, "desc"),
        ]
        productid = _xml_attr(s, "productid")
        md5 = _xml_attr(s, "md5")
        if "stiker_" in productid:
            candidates.append(productid.rsplit("stiker_", 1)[-1][:8])
        if md5:
            candidates.append(md5[:8])
        # 有些字段值是 base64（例如 ChEKB2RlZmF1bHQSBuacjeS6hg==），
        # 直接显示很丑，挑第一个"看起来是人话"的候选
        name = next((c.strip() for c in candidates
                     if c and c.strip() and not _looks_base64(c.strip())), "")
        return {"kind": "sticker", "label": "表情",
                "text": f"[表情 {name}]" if name else "[表情]",
                "meta": {"name": name or None, "md5": md5, "productid": productid},
                "countable": True}

    if kind == "quote":
        s = _as_text(content)
        title = _xml_field(s, "title")
        quoted = _xml_field(s, "content")
        if not title and not quoted:
            return {"kind": "quote", "label": "引用", "text": "[引用]",
                    "meta": {}, "countable": True}
        text = f"[引用] {title}" if title else "[引用]"
        return {"kind": "quote", "label": "引用", "text": text[:max_len],
                "meta": {"quoted": quoted[:200]}, "countable": True}

    if kind == "app":
        s = _as_text(content)
        title = _xml_field(s, "title")
        url = _xml_field(s, "url")
        apptype = _xml_field(s, "type")
        subtype = ""
        try:
            root = ET.fromstring(s)
            subtype = root.get("appid", "")
        except Exception:
            pass
        if not title:
            title = "[链接]" if url else "[卡片消息]"
        return {"kind": "app", "label": "链接/文件", "text": title[:max_len],
                "meta": {"url": url, "apptype": apptype, "appid": subtype}, "countable": True}

    if kind == "card":
        s = _as_text(content)
        nick = _xml_field(s, "nickname")
        return {"kind": "card", "label": "名片", "text": f"[名片 {nick}]" if nick else "[名片]",
                "meta": {"nickname": nick}, "countable": True}

    if kind == "location":
        s = _as_text(content)
        return {"kind": "location", "label": "位置",
                "text": _xml_field(s, "label") or "[位置]", "meta": {}, "countable": True}

    if kind == "voip":
        return {"kind": "voip", "label": "通话", "text": "[通话]",
                "meta": {}, "countable": False}

    if kind == "friend_request":
        return {"kind": "friend_request", "label": "好友申请", "text": "[好友申请]",
                "meta": {}, "countable": False}

    if kind in ("system", "revoke"):
        s = _as_text(content)
        # 微信系统消息常带 XML 包装，取可读部分
        if _looks_like_xml(s):
            s = _xml_field(s, "text") or _xml_field(s, "content") or ""
        return {"kind": kind, "label": label, "text": (s or f"[{label}]")[:max_len],
                "meta": {}, "countable": False}

    return {"kind": kind, "label": label, "text": f"[{label}]",
            "meta": {}, "countable": False}


def _selftest() -> int:
    bad = 0

    def chk(name, cond, extra=""):
        nonlocal bad
        if not cond:
            print(f"FAIL {name} {extra}")
            bad += 1

    r = decode(1, "你好世界")
    chk("文本", r["kind"] == "text" and r["text"] == "你好世界" and r["countable"], r)

    r = decode(1, None)
    chk("空文本不炸", r["kind"] == "text", r)

    r = decode(3, b"\x28\xb5\x2f\xfd\x00garbage")
    chk("图片占位", r["kind"] == "image" and r["text"] == "[图片]", r)

    r = decode(47, '<msg><emoji name="偷笑" md5="abc"/></msg>')
    chk("表情取名字", "偷笑" in r["text"], r)

    r = decode(47, b"\x28\xb5\x2f\xfd\xff\xff\xff\xff")
    chk("坏zstd不炸", r["kind"] == "sticker", r)

    r = decode(34, "<msg><voicelength>3200</voicelength></msg>")
    chk("语音秒数", "3" in r["text"], r)

    r = decode(10000, "<sysmsg><text>你撤回了一条消息</text></sysmsg>")
    chk("系统消息不可计费", r["countable"] is False and "撤回" in r["text"], r)

    r = decode(49, "<msg><appmsg><title>一篇好文章</title><url>https://x.com/a</url></appmsg></msg>")
    chk("链接取标题", r["text"] == "一篇好文章", r)

    r = decode(999999, "未知类型但是文本")
    chk("未知类型兜底", r["kind"] == "text", r)

    r = decode(47, '<msg><emoji fromusername="wxid_a" md5="18305b8e5e31767e34e058ec8754448f" '
                   'productid="com.tencent.xin.emoticon.person.stiker_1734276817a40a19" '
                   'androidmd5="18305b8e5e31767e34e058ec8754448f" len="15063"></emoji></msg>')
    chk("表情取md5短码", "18305b8e" in r["text"] or "17342768" in r["text"], r)

    r = decode(47, '<msg><emoji name="偷笑" md5="abc"></emoji></msg>')
    chk("表情优先用名字", "偷笑" in r["text"], r)

    r = decode(244813135921, '<?xml version="1.0"?><msg><appmsg><title>你这个死装的</title>'
                             '<type>57</type><refermsg><content>引用的话</content></refermsg></appmsg></msg>')
    chk("引用消息", r["kind"] == "quote" and "你这个死装的" in r["text"], r)

    r = decode(244813135921, b"\x28\xb5\x2f\xfd\x00\x00\x00\x00")
    chk("坏引用不炸", r["kind"] == "quote", r)

    if not bad:
        print("OK wcmsg 自测全部通过")
    return bad


if __name__ == "__main__":
    raise SystemExit(1 if _selftest() else 0)
