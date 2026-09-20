# -*- coding: utf-8 -*-
"""
wcllm.py — LLM 统一封装（DeepSeek）

踩过的坑，都写在这里免得再犯：
  · **max_tokens 要给够**：deepseek-flash 等思考模型会把预算先花在 reasoning 上，
    max_tokens 太小会导致 content 为空（实测 500 时输出全空，2000 时 reasoning 就吃掉 1999）。
  · **结构化抽取用 deepseek-chat**：它是唯一不产出 reasoning tokens 的模型，
    0.8 秒直接出 JSON，便宜且稳。思考模型做抽取既慢又容易空输出。
  · **JSON 要抠**：模型经常包 ```json 代码块或加解释，必须容错解析。
  · 计费口径与仪表盘一致（输入/输出/缓存分开算）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DEFAULT_BASE = "https://api.deepseek.com/v1"
# 抽取/分类用它：无 reasoning、快、便宜
MODEL_FAST = "deepseek-chat"
# 需要深度理解时用它
MODEL_DEEP = "deepseek-flash"


def _load_key() -> str:
    """按优先级找 key：环境变量 → 项目里的 draft.json → ~/.dsh/.env"""
    k = os.environ.get("DS_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k.strip()
    for p in (os.path.join(ROOT, "draft.json"),
              os.path.join(os.path.expanduser("~"), ".dsh", ".env")):
        try:
            if p.endswith(".json"):
                with open(p, encoding="utf-8") as f:
                    v = json.load(f).get("api_key")
                    if v:
                        return v.strip()
            else:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        m = re.match(r"\s*(?:DS_KEY|DEEPSEEK_API_KEY)\s*=\s*(\S+)", line)
                        if m:
                            return m.group(1).strip().strip('"\'')
        except Exception:
            continue
    return ""


class LLM:
    def __init__(self, base_url: str = DEFAULT_BASE, api_key: str = "",
                 model: str = MODEL_FAST, verbose: bool = False):
        self.base = (base_url or DEFAULT_BASE).rstrip("/")
        self.key = api_key or _load_key()
        self.model = model
        self.verbose = verbose
        self.usage = {"calls": 0, "in": 0, "out": 0, "reasoning": 0, "cache_hit": 0}
        if not self.key:
            raise RuntimeError(
                "找不到 API key。请设置环境变量 DS_KEY，"
                "或在微信token/draft.json 里填 api_key")

    # ---------------- 基础调用 ----------------

    def chat(self, messages, model=None, max_tokens=4000, temperature=0.3,
             retries=2) -> str:
        body = json.dumps({
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode()
        last = None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(
                    self.base + "/chat/completions", data=body, method="POST",
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + self.key})
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=180) as r:
                    d = json.loads(r.read().decode())
                u = d.get("usage") or {}
                self.usage["calls"] += 1
                self.usage["in"] += u.get("prompt_tokens", 0)
                self.usage["out"] += u.get("completion_tokens", 0)
                self.usage["reasoning"] += (u.get("completion_tokens_details") or {}) \
                    .get("reasoning_tokens", 0) or 0
                self.usage["cache_hit"] += u.get("prompt_cache_hit_tokens", 0) or 0
                content = (d["choices"][0]["message"].get("content") or "").strip()
                if self.verbose:
                    print(f"    [llm] {model or self.model} {time.time()-t0:.1f}s "
                          f"in={u.get('prompt_tokens')} out={u.get('completion_tokens')}",
                          flush=True)
                if not content:
                    # 输出为空 = 预算被思考吃光，加倍重试
                    last = "空输出（可能 max_tokens 被 reasoning 吃光）"
                    max_tokens = min(max_tokens * 3, 16000)
                    if self.verbose:
                        print(f"    [llm] {last}，加大到 {max_tokens} 重试", flush=True)
                    continue
                return content
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "ignore")[:200]
                except Exception:
                    pass
                last = f"HTTP {e.code} {detail}"
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败: {last}")

    # ---------------- 结构化输出 ----------------

    def chat_json(self, system: str, user: str, model=None, max_tokens=4000,
                  temperature=0.1):
        """要求模型输出 JSON，并容错解析。解析失败返回 None。"""
        raw = self.chat([{"role": "system", "content": system},
                         {"role": "user", "content": user}],
                        model=model or MODEL_FAST, max_tokens=max_tokens,
                        temperature=temperature)
        return parse_json(raw)

    def cost(self, price_in=0.5, price_out=2.0, price_cache=0.05) -> dict:
        """按给定单价（元/百万）估算本次会话累计花费。"""
        u = self.usage
        miss = max(u["in"] - u["cache_hit"], 0)
        c = (miss / 1e6 * price_in + u["cache_hit"] / 1e6 * price_cache
             + u["out"] / 1e6 * price_out)
        # 思考 token 已含在 completion 里，不重复计
        return {"calls": u["calls"], "in": u["in"], "out": u["out"],
                "reasoning": u["reasoning"], "cache_hit": u["cache_hit"],
                "cost": round(c, 6)}


def parse_json(raw: str):
    """从模型输出里抠出 JSON（容错：代码块、前后解释、单引号）。"""
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    for pat in (r"\[.*\]", r"\{.*\}"):
        m = re.search(pat, s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return None


def selftest():
    llm = LLM(verbose=True)
    print("模型:", llm.model)
    print()
    print("[1] 普通对话")
    print("   ", llm.chat([{"role": "user", "content": "用一句话说明什么是缓存命中"}],
                         max_tokens=500)[:80])
    print()
    print("[2] 结构化抽取（用 deepseek-chat，无 reasoning）")
    convo = ("对方: 周五之前把报告发我\n我: 好\n对方: 记得带充电线")
    out = llm.chat_json(
        '抽取待办。只输出 JSON 数组，元素 {"type","who","what","when"}。',
        convo, model=MODEL_FAST, max_tokens=1500)
    print("   ", json.dumps(out, ensure_ascii=False) if out else "解析失败")
    print()
    print("[3] 用量与成本")
    print("   ", llm.cost())


if __name__ == "__main__":
    selftest()
