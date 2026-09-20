# -*- coding: utf-8 -*-
"""
wcdraft.py — 拟回复（**只生成文字，绝不自动发送**）

流程：
  1. 读 style_persona.md（你的说话风格档案）+ 该会话里你自己说过的真实样本
  2. 拼成 prompt，调模型生成 1~3 条候选回复
  3. 显示在仪表盘里，你**自己**复制粘贴发出去

为什么不做自动发送：
  微信 4.x 是 Qt 应用，UI Automation 读不到控件（实测整棵树只有 3 个节点），
  要自动发送只能模拟键盘或注入进程，两者都触碰服务条款且有封号风险。
  这个模块刻意把边界停在"生成文本"。

模型接入：
  · 走 OpenAI 兼容的 /chat/completions 接口，base_url 和 key 都能配
  · 没配 key 时自动降级为**本地模板**（不需要联网，也能给出可用草稿）
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DRAFT_CFG = os.path.join(HERE, "draft.json")
PERSONA_FILE = os.path.join(HERE, "style_persona.md")
TEXTS_FILE = os.path.join(HERE, "my_texts.jsonl")

DEFAULT_CFG = {
    "enabled": True,
    "base_url": "",          # 例如 https://api.deepseek.com/v1
    "api_key": "",           # 留空则用本地模板
    "model": "deepseek-v4-flash",
    "temperature": 1.1,      # 高一点，避免每句都一样
    "max_tokens": 300,
    "candidates": 3,         # 一次给几个候选
    "use_persona": True,
    "extra_instruction": "",  # 你自己加的额外要求
    "timeout": 30,
}


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(DRAFT_CFG):
        try:
            with open(DRAFT_CFG, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_cfg(cfg: dict) -> dict:
    with open(DRAFT_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    return cfg


def load_persona() -> str:
    """优先用手写打磨过的风格档案（PERSONA_*.md），
    退回 wcstyle.py 自动生成的那份（style_persona.md）。

    手写那份有真实的语言指纹和反例（比如"你从不说哈哈"），
    比纯统计更能约束模型，所以优先级更高。
    """
    import glob
    hand = sorted(glob.glob(os.path.join(HERE, "PERSONA_*.md")))
    if hand:
        with open(hand[0], encoding="utf-8") as f:
            return f.read()
    if os.path.exists(PERSONA_FILE):
        with open(PERSONA_FILE, encoding="utf-8") as f:
            return f.read()
    return ""


def my_samples_for(wxid: str, limit: int = 14) -> list:
    """取我在这个会话里说过的真实句子，给模型当"语感样本"。"""
    if not os.path.exists(TEXTS_FILE):
        return []
    out = []
    with open(TEXTS_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("s") == wxid:
                t = (r.get("x") or "").strip()
                if 1 <= len(t) <= 40 and not t.startswith("http"):
                    out.append(t)
    if len(out) > limit:
        # 取最近的一段，但打散一点，避免全是同一段对话
        tail = out[-limit * 3:]
        out = random.sample(tail, limit)
    return out


def _trim_persona(text: str, max_chars: int = 3200) -> str:
    """档案太长会挤爆上下文。优先保留语言指纹与写作规则这几段。"""
    if len(text) <= max_chars:
        return text
    # 手写档案按 "## 标题" 分段，挑最关键的三段：长度、情绪表达、写作规则
    parts = re.split(r"\n(?=## )", text)
    want = ("长度", "情绪", "写作规则", "标点", "特色表达")
    keep = [p for p in parts if any(w in p.split("\n")[0] for w in want)]
    out = "\n".join(keep) if keep else text
    return out[:max_chars]


def build_prompt(chat_name: str, context: list, samples: list, cfg: dict,
                 topic: str = "") -> list:
    """组装 messages。context 是最近的对话 [{dir, sender, text}, ...]。"""
    persona = _trim_persona(load_persona()) if cfg.get("use_persona", True) else ""
    sys_msg = [
        "你在帮我起草微信回复。要求：",
        "1. 用**我本人**的语气写，就像我自己在打字，不要有 AI 味，不要客服腔。",
        "2. 短。我平时平均只发 7~8 个字，中位数 5 个字，很多回复就两三个字。",
        "3. 不要每句都用句号收尾（我 91% 的消息不加句尾标点）。",
        "4. 不要解释你在做什么，不要客套总结，不要加「希望有帮助」这类话。",
        "5. 只输出回复正文，不要引号、不要编号、不要任何前后缀说明。",
    ]
    if persona:
        sys_msg += ["", "以下是我的说话风格档案（真实统计得出）：", "", persona]
    if samples:
        sys_msg += ["", "我在这个会话里说过的真实句子（语感参考）："]
        sys_msg += [f"- {s}" for s in samples]

    if cfg.get("extra_instruction"):
        sys_msg += ["", "额外要求：" + cfg["extra_instruction"]]

    user_lines = [f"这是我和「{chat_name}」的最近对话：", ""]
    for m in context[-16:]:
        who = "我" if m.get("dir") == "out" else (m.get("sender") or chat_name)
        user_lines.append(f"{who}: {m.get('text', '')}")
    user_lines += ["", "请给我 3 条候补回复，每行一条，从短到长排列。"]
    if topic:
        user_lines.append(f"（我想表达的意思大致是：{topic}）")

    return [{"role": "system", "content": "\n".join(sys_msg)},
            {"role": "user", "content": "\n".join(user_lines)}]


def _post_chat(cfg: dict, messages: list) -> str:
    """调 OpenAI 兼容接口。"""
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("没有配置 base_url")
    url = base + "/chat/completions"
    body = json.dumps({
        "model": cfg.get("model"),
        "messages": messages,
        "temperature": float(cfg.get("temperature", 1.1)),
        "max_tokens": int(cfg.get("max_tokens", 300)),
        "n": 1,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (cfg.get("api_key") or ""),
    })
    with urllib.request.urlopen(req, timeout=int(cfg.get("timeout", 30))) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------
# 本地模板降级：没配 key 时也能给出可用的草稿
# --------------------------------------------------------------------------

TEMPLATES = {
    "greeting": ["在的", "嗯在", "怎么了"],
    "question": ["我看看", "稍等我想想", "这个我得看下", "不太确定诶"],
    "invite": ["可以", "行", "什么时候", "我看下时间"],
    "thanks": ["客气了", "不赖", "没事"],
    "laugh": ["哈哈哈哈", "你这也太逗了"],
    "long": ["我大概明白你意思了", "这样啊", "那你说咋办"],
    "default": ["嗯", "可以", "知道了", "我看下", "晚点回你", "不赖"],
}


def _bucket(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "default"
    if re.search(r"哈|笑|😂|🤣", t):
        return "laugh"
    if t.endswith(("?", "？")) or re.search(r"吗|怎么|为什么|啥|什么|几点|哪", t):
        return "question"
    if re.search(r"谢谢|辛苦|感谢", t):
        return "thanks"
    if re.search(r"来|去|吃|聚|约|一起|见面|打球|有空", t):
        return "invite"
    if len(t) > 30:
        return "long"
    return "default"


def local_draft(context: list, n: int = 3) -> list:
    """不联网的降级方案：按对方那句话的类型给几个我常用的短回复。"""
    last_in = ""
    for m in reversed(context):
        if m.get("dir") != "out":
            last_in = m.get("text") or ""
            break
    pool = TEMPLATES.get(_bucket(last_in), TEMPLATES["default"])
    picks = pool[:n]
    while len(picks) < n:
        picks.append(random.choice(TEMPLATES["default"]))
    return picks


def generate(chat_name: str, context: list, wxid: str, topic: str = "",
             n: int = None) -> dict:
    """生成候选回复。返回 {candidates, source, note}。"""
    cfg = load_cfg()
    n = n or int(cfg.get("candidates", 3))
    if not cfg.get("enabled", True):
        return {"candidates": [], "source": "off", "note": "拟回复已在配置里关闭"}

    samples = my_samples_for(wxid)
    use_remote = bool(cfg.get("base_url") and cfg.get("api_key"))
    if use_remote:
        try:
            msgs = build_prompt(chat_name, context, samples, cfg, topic)
            txt = _post_chat(cfg, msgs)
            cands = []
            for line in txt.splitlines():
                line = line.strip()
                line = re.sub(r"^\s*(?:\d+[.、)]|[-*•])\s*", "", line)
                line = line.strip('"“”\' ')
                if line and len(line) <= 200:
                    cands.append(line)
            if cands:
                return {"candidates": cands[:n], "source": "model",
                        "model": cfg.get("model"),
                        "note": f"由 {cfg.get('model')} 生成，已注入你的风格档案与 "
                                f"{len(samples)} 条真实样本"}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            fb = local_draft(context, n)
            return {"candidates": fb, "source": "local",
                    "note": f"模型调用失败（HTTP {e.code} {detail}），已降级为本地模板"}
        except Exception as e:
            fb = local_draft(context, n)
            return {"candidates": fb, "source": "local",
                    "note": f"模型调用失败（{type(e).__name__}: {e}），已降级为本地模板"}

    fb = local_draft(context, n)
    return {"candidates": fb, "source": "local", "samples": len(samples),
            "note": "还没配置模型（draft.json 里的 base_url / api_key），"
                    "先用本地模板；填上就能按你的风格生成"}


if __name__ == "__main__":
    # 自测：本地模板 + prompt 组装
    ctx = [{"dir": "in", "sender": "小王", "text": "明天下午有空吗？一起去打球"},
           {"dir": "out", "text": "我看看"},
           {"dir": "in", "sender": "小王", "text": "怎么样"}]
    r = generate("小王", ctx, "demo_x")
    print("本地草稿:", r["candidates"], "|", r["note"])
    p = build_prompt("小王", ctx, ["我看下时间", "不赖"], load_cfg())
    print()
    print("system prompt 长度:", len(p[0]["content"]), "字符")
    print("user prompt:")
    print(p[1]["content"])
