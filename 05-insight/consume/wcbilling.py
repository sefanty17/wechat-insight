#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wcbilling.py — 把对话物化成钱

核心设计：**金额永远从消息账本实时算出来，不维护一份会漂移的累计值。**
聊天记录是不可变的，token 是确定性函数，所以费用 = f(账本, 价目表)，天然可重算、可审计。
额度/分配是"配置"，费用是"推导结果"，两者分开，避免出现"账单对不上"的经典问题。

账务口径：
    输入费用 = 输入tokens / 1e6 * 输入单价
    输出费用 = 输出tokens / 1e6 * 输出单价
    缓存费用 = 命中缓存的输入tokens / 1e6 * 缓存单价
    思考费用 = 思考tokens / 1e6 * 输出单价 * 思考倍率
    合计     = 输入 + 输出 + 缓存 + 思考
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "billing.json")
MARKET_FILE = os.path.join(HERE, "market_extra.json")

M = 1_000_000

# --------------------------------------------------------------------------
# 内置模型广场（价格单位：元 / 百万 tokens）
# 数据取自公开价目，用户可以在界面上改，也可以自己加模型。
# --------------------------------------------------------------------------

BUILTIN_MODELS = [
    {"id": "deepseek-v4-flash", "vendor": "DeepSeek", "tag": "推荐",
     "name": "deepseek-v4-flash", "input": 0.5, "output": 2.0, "cache": 0.05,
     "reasoning_mult": 1.0, "context": "128K", "note": "便宜大碗，日常聊天够用"},
    {"id": "deepseek-v4-pro", "vendor": "DeepSeek", "tag": "旗舰",
     "name": "deepseek-v4-pro", "input": 2.0, "output": 8.0, "cache": 0.2,
     "reasoning_mult": 1.0, "context": "128K", "note": "复杂推理更强"},
    {"id": "deepseek-reasoner", "vendor": "DeepSeek", "tag": "思考",
     "name": "deepseek-reasoner", "input": 1.0, "output": 16.0, "cache": 0.1,
     "reasoning_mult": 1.0, "context": "64K", "note": "长思考，输出贵在思考上"},
    {"id": "claude-sonnet-4", "vendor": "Anthropic", "tag": "",
     "name": "claude-sonnet-4", "input": 3.0, "output": 15.0, "cache": 0.3,
     "reasoning_mult": 1.0, "context": "200K", "note": "均衡"},
    {"id": "claude-opus-4", "vendor": "Anthropic", "tag": "旗舰",
     "name": "claude-opus-4", "input": 15.0, "output": 75.0, "cache": 1.5,
     "reasoning_mult": 1.0, "context": "200K", "note": "贵，慎用"},
    {"id": "gpt-4o", "vendor": "OpenAI", "tag": "",
     "name": "gpt-4o", "input": 18.0, "output": 72.0, "cache": 9.0,
     "reasoning_mult": 1.0, "context": "128K", "note": "老朋友"},
    {"id": "gpt-4o-mini", "vendor": "OpenAI", "tag": "便宜",
     "name": "gpt-4o-mini", "input": 1.0, "output": 4.0, "cache": 0.5,
     "reasoning_mult": 1.0, "context": "128K", "note": "轻量"},
    {"id": "gemini-2.5-pro", "vendor": "Google", "tag": "",
     "name": "gemini-2.5-pro", "input": 1.25, "output": 10.0, "cache": 0.31,
     "reasoning_mult": 1.0, "context": "1M", "note": "超长上下文"},
]

DEFAULT_CONFIG = {
    "currency": "CNY",
    "total_quota": 500.0,        # 总金额（充值总额）
    "active_key": None,          # 全局默认用于计价的 key
    "active_model": "deepseek-v4-flash",
    "keys": [],
    # 按人计费：{wxid: key_id}。分配后该会话的全部历史都会按这个 Key 的模型重算。
    "person_keys": {},
    "alerts": {"warn_ratio": 0.8, "hard_stop": False},
    "updated": None,
}


# --------------------------------------------------------------------------
# 配置读写
# --------------------------------------------------------------------------

def load_json(path: str, default):
    """读 JSON。文件坏了不静默吞掉——先把坏文件留个备份再报错，
    否则用户会莫名丢失全部 Key/配置，而且完全不知道发生了什么。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        bak = f"{path}.broken"
        try:
            shutil.copy2(path, bak)
        except OSError:
            pass
        raise RuntimeError(
            f"{os.path.basename(path)} 内容损坏，无法解析（{e}）。"
            f"已备份到 {os.path.basename(bak)}，请修复或删除该文件后重试。"
        ) from e
    except OSError:
        return default


def save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_FILE, {}))
    cfg.setdefault("keys", [])
    cfg.setdefault("person_keys", {})
    if not cfg.get("keys"):
        # 第一次运行：给一个默认 key，让界面不是空的
        cfg["keys"] = [{
            "id": uuid.uuid4().hex[:12],
            "label": "默认额度",
            "masked": "sk-••••••••••••••••",
            "quota": cfg.get("total_quota", 500.0),
            "model": cfg.get("active_model", "deepseek-v4-flash"),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "初始额度，可在界面上改",
        }]
        cfg["active_key"] = cfg["keys"][0]["id"]
        save_config(cfg)
    if not cfg.get("active_key") or not any(k["id"] == cfg["active_key"] for k in cfg["keys"]):
        cfg["active_key"] = cfg["keys"][0]["id"]
    return cfg


def save_config(cfg: dict) -> None:
    cfg["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_json(CONFIG_FILE, cfg)


# --------------------------------------------------------------------------
# 模型广场
# --------------------------------------------------------------------------

def load_models() -> list:
    extra = load_json(MARKET_FILE, [])
    seen = {m["id"] for m in BUILTIN_MODELS}
    return BUILTIN_MODELS + [m for m in extra if m.get("id") not in seen]


def save_custom_model(model: dict) -> dict:
    extra = load_json(MARKET_FILE, [])
    model = dict(model)
    model["id"] = model.get("id") or ("custom-" + uuid.uuid4().hex[:8])
    model["custom"] = True
    extra = [m for m in extra if m.get("id") != model["id"]] + [model]
    save_json(MARKET_FILE, extra)
    return model


def delete_custom_model(model_id: str) -> bool:
    extra = load_json(MARKET_FILE, [])
    left = [m for m in extra if m.get("id") != model_id]
    if len(left) == len(extra):
        return False
    save_json(MARKET_FILE, left)
    return True


def get_model(model_id: str) -> dict:
    for m in load_models():
        if m["id"] == model_id:
            return m
    return BUILTIN_MODELS[0]


def update_model_price(model_id: str, price: dict) -> dict:
    """改价：内置模型也允许改（写进 market_extra 覆盖）。"""
    extra = load_json(MARKET_FILE, [])
    base = get_model(model_id)
    merged = dict(base)
    for k in ("input", "output", "cache", "reasoning_mult"):
        if k in price and price[k] is not None:
            merged[k] = float(price[k])
    merged["id"] = model_id
    merged["custom"] = bool(base.get("custom"))
    extra = [m for m in extra if m.get("id") != model_id] + [merged]
    save_json(MARKET_FILE, extra)
    return merged


# --------------------------------------------------------------------------
# 计价
# --------------------------------------------------------------------------

def price_usage(model: dict, usage: dict) -> dict:
    """把 token 用量算成钱。返回明细，单位元。"""
    mi = model.get("input", 0.0) / M
    mo = model.get("output", 0.0) / M
    mc = model.get("cache", model.get("input", 0.0)) / M
    mult = float(model.get("reasoning_mult", 1.0) or 1.0)

    c_in = usage.get("input", 0.0) * mi
    c_out = usage.get("output", 0.0) * mo
    # 命中缓存的输入部分按缓存价，其余按输入价
    cache_hit = usage.get("cache_hit", 0.0)
    c_cache = cache_hit * mc
    c_in_net = max(usage.get("input", 0.0) - cache_hit, 0.0) * mi
    c_think = usage.get("reasoning", 0.0) * mo * mult
    total = c_in_net + c_out + c_cache + c_think
    return {
        "in": round(c_in_net, 8), "out": round(c_out, 8),
        "cache": round(c_cache, 8), "think": round(c_think, 8),
        "total": round(total, 8),
        "raw_in": round(c_in, 8),
    }


def cost_of_message(msg: dict, model: dict) -> float:
    """单条消息的费用 = 模型价 × 该消息 token。msg 需含 input/output/reasoning/cache_hit。"""
    return price_usage(model, msg)["total"]


def aggregate(messages: list, model: dict) -> dict:
    """把一组消息按模型价汇总成账单。"""
    acc = {"in": 0.0, "out": 0.0, "cache": 0.0, "think": 0.0, "total": 0.0,
           "input": 0.0, "output": 0.0, "reasoning": 0.0, "cache_hit": 0.0, "n": 0}
    for m in messages:
        p = price_usage(model, m)
        for k in ("in", "out", "cache", "think", "total"):
            acc[k] += p[k]
        for k in ("input", "output", "reasoning", "cache_hit"):
            acc[k] += m.get(k, 0.0)
        acc["n"] += 1
    for k in acc:
        acc[k] = round(acc[k], 6) if k != "n" else acc[k]
    return acc


# --------------------------------------------------------------------------
# 核算：账本 × 配置 → 余额 / 每 key 用量
# --------------------------------------------------------------------------

def compute_ledger_bill(ledger: list, cfg: dict, models: list) -> dict:
    """账本 → 总消费。

    计价模型按优先级解析（这是「按人计费」的核心）：
      1. 该条消息的 wxid 在 person_keys 里分配了 Key → 用那个 Key 绑定的模型
         （所以给某个会话塞 Key，它的**全部历史**立刻按新模型重算）
      2. 消息自带 model 字段（pin 过的）
      3. 全局 active_model

    性能：这是全站最热的函数，而账本是**只增不改**的，所以结果可以被缓存。
    10 万条账本一次全量算价约 1.5 秒——每次请求都重算会让界面明显卡顿。
    缓存键包含账本长度 + 全部会影响价格的配置，因此追加消息、改价、换模型、
    改 Key 分配都会自动失效。
    """
    key = _bill_cache_key(ledger, cfg)
    hit = _BILL_CACHE.get(key)
    if hit is not None:
        return hit

    by_id = {m["id"]: m for m in models}
    fallback_model = by_id.get(cfg.get("active_model")) or models[0]
    key_models = {k["id"]: by_id.get(k.get("model")) or fallback_model
                  for k in cfg.get("keys", [])}
    person_keys = cfg.get("person_keys", {}) or {}

    total = 0.0
    by_key = {}
    by_model = {}
    by_person = {}
    for e in ledger:
        wxid = e.get("wxid")
        pkey = person_keys.get(wxid) if wxid else None
        kid = e.get("key_id") or pkey or cfg.get("active_key")
        emodel = by_id.get(e.get("model"))
        if pkey and pkey in key_models:
            model = key_models[pkey]          # 按人分配优先
        elif emodel:
            model = emodel                    # 消息自带（pin 过）
        else:
            model = key_models.get(kid) or fallback_model
        c = price_usage(model, e)
        cost = c["total"]
        total += cost
        b = by_key.setdefault(kid, {"cost": 0.0, "n": 0, "in": 0.0, "out": 0.0})
        b["cost"] += cost
        b["n"] += 1
        b["in"] += e.get("input", 0.0)
        b["out"] += e.get("output", 0.0)
        mstat = by_model.setdefault(model["id"], {"cost": 0.0, "n": 0})
        mstat["cost"] += cost
        mstat["n"] += 1
        if wxid:
            ps = by_person.setdefault(wxid, {"cost": 0.0, "n": 0})
            ps["cost"] += cost
            ps["n"] += 1

    keys_out = []
    for k in cfg.get("keys", []):
        used = by_key.get(k["id"], {"cost": 0.0, "n": 0, "in": 0.0, "out": 0.0})
        quota = float(k.get("quota", 0.0))
        keys_out.append({
            **k,
            "used": round(used["cost"], 6),
            "remaining": round(quota - used["cost"], 6),
            "used_ratio": round(used["cost"] / quota, 6) if quota > 0 else 0.0,
            "msgs": used["n"],
            "tokens_in": round(used["in"], 2),
            "tokens_out": round(used["out"], 2),
        })

    quota_total = float(cfg.get("total_quota", 0.0))
    result = {
        "total_quota": round(quota_total, 4),
        "total_spent": round(total, 4),
        "remaining": round(quota_total - total, 4),
        "used_ratio": round(total / quota_total, 4) if quota_total > 0 else 0.0,
        "keys": keys_out,
        "by_model": {k: {"cost": round(v["cost"], 4), "n": v["n"]}
                     for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])},
        # 按人明细：只回传前 30 名，避免每次请求都带上几百个会话把响应撑大
        # （完整排行在 /api/breakdown 里）
        "by_person": {k: {"cost": round(v["cost"], 6), "n": v["n"]}
                      for k, v in sorted(by_person.items(),
                                         key=lambda kv: -kv[1]["cost"])[:30]},
        "ledger_entries": len(ledger),
        "cost_usd_hint": round(total / 7.1, 4),
    }
    _BILL_CACHE.clear()          # 只留最新一份，避免内存无界增长
    _BILL_CACHE[key] = result
    return result


_BILL_CACHE: dict = {}


def _bill_cache_key(ledger: list, cfg: dict) -> tuple:
    """缓存键：账本长度 + 所有影响价格的配置 + 价目表文件指纹。"""
    try:
        mt = os.path.getmtime(MARKET_FILE)
    except OSError:
        mt = 0.0
    return (
        len(ledger),
        cfg.get("active_model"),
        cfg.get("active_key"),
        cfg.get("total_quota"),
        mt,
        tuple(sorted(
            (k.get("id"), k.get("quota"), k.get("model")) for k in cfg.get("keys", [])
        )),
        tuple(sorted((cfg.get("person_keys") or {}).items())),
    )


def assign_person_key(cfg: dict, wxid: str, key_id: str | None) -> dict:
    """给某个会话（人/群）分配一个 API Key；key_id 为 None 表示取消分配。

    分配后该会话的**所有历史消息**都会按这个 Key 绑定的模型重算——
    因为账本只存 token，模型是查询时解析的。
    """
    pk = dict(cfg.get("person_keys") or {})
    if key_id:
        if not any(k["id"] == key_id for k in cfg.get("keys", [])):
            raise KeyError(f"没有这个 key: {key_id}")
        pk[wxid] = key_id
    else:
        pk.pop(wxid, None)
    cfg["person_keys"] = pk
    save_config(cfg)
    invalidate_bill_cache()
    return pk


def person_bill_view(cfg: dict, wxid: str, models: list) -> dict:
    """某个会话当前的计费归属：用的哪个 Key、哪个模型、单价多少。"""
    key_id = (cfg.get("person_keys") or {}).get(wxid)
    key = next((k for k in cfg.get("keys", []) if k["id"] == key_id), None)
    model_id = (key or {}).get("model") or cfg.get("active_model")
    model = get_model(model_id) if model_id else models[0]
    return {
        "wxid": wxid,
        "key_id": key_id,
        "key_label": (key or {}).get("label"),
        "source": "person" if key else "global",
        "model_id": model["id"],
        "model_name": model["name"],
        "price": {"input": model["input"], "output": model["output"],
                  "cache": model.get("cache", 0)},
        "quota": (key or {}).get("quota"),
    }


def invalidate_bill_cache() -> None:
    """改价 / 换模型后主动清缓存（缓存键其实已经能覆盖，这里是双保险）。"""
    _BILL_CACHE.clear()


# --------------------------------------------------------------------------
# 配置变更
# --------------------------------------------------------------------------

MAX_QUOTA = 10_000_000.0      # 单值上限，挡住明显的误输入


def _num(value, default=0.0, lo=0.0, hi=MAX_QUOTA):
    """把输入安全地转成合法数字（挡掉字符串、负数、离谱的大数）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):   # NaN / inf
        return default
    return max(lo, min(hi, v))


def _text(value, default="", limit=60):
    if not isinstance(value, str):
        return default
    v = value.strip()
    return v[:limit] if v else default


def set_quota(cfg: dict, total: float | None = None) -> dict:
    if total is not None:
        cfg["total_quota"] = _num(total, cfg.get("total_quota", 0.0))
    save_config(cfg)
    invalidate_bill_cache()
    return cfg


def add_key(cfg: dict, label: str, quota: float, masked: str = "", model: str = "",
            note: str = "") -> dict:
    quota = _num(quota, 0.0)
    # 分配额度超过总额度时给个提示（不阻止，用户可能有自己的理由）
    allocated = sum(_num(k.get("quota", 0)) for k in cfg.get("keys", []))
    k = {
        "id": uuid.uuid4().hex[:12],
        "label": _text(label, f"Key {len(cfg.get('keys', [])) + 1}"),
        "masked": _text(masked, "sk-••••" + uuid.uuid4().hex[:6]),
        "quota": quota,
        "model": _text(model, cfg.get("active_model") or "", 80),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": _text(note, "", 200),
    }
    cfg.setdefault("keys", []).append(k)
    if not cfg.get("active_key"):
        cfg["active_key"] = k["id"]
    save_config(cfg)
    invalidate_bill_cache()
    return {"key": k, "over_allocated": allocated + quota > _num(cfg.get("total_quota", 0))}


def update_key(cfg: dict, key_id: str, patch: dict) -> dict | None:
    for k in cfg.get("keys", []):
        if k["id"] == key_id:
            if "label" in patch and patch["label"] is not None:
                k["label"] = _text(patch["label"], k["label"])
            if "quota" in patch and patch["quota"] is not None:
                k["quota"] = _num(patch["quota"], k.get("quota", 0.0))
            if "model" in patch and patch["model"] is not None:
                k["model"] = _text(patch["model"], k.get("model") or "", 80)
            if "note" in patch and patch["note"] is not None:
                k["note"] = _text(patch["note"], "", 200)
            if "masked" in patch and patch["masked"] is not None:
                k["masked"] = _text(patch["masked"], k.get("masked") or "", 60)
            save_config(cfg)
            invalidate_bill_cache()
            return k
    return None


def delete_key(cfg: dict, key_id: str) -> bool:
    before = len(cfg.get("keys", []))
    cfg["keys"] = [k for k in cfg.get("keys", []) if k["id"] != key_id]
    if len(cfg["keys"]) == before:
        return False
    if cfg.get("active_key") == key_id:
        cfg["active_key"] = cfg["keys"][0]["id"] if cfg["keys"] else None
    # 顺手清掉指向这个 Key 的按人分配，避免留下悬空引用
    pk = cfg.get("person_keys") or {}
    cfg["person_keys"] = {w: k for w, k in pk.items() if k != key_id}
    save_config(cfg)
    invalidate_bill_cache()
    return True


def set_active(cfg: dict, key_id: str = None, model: str = None) -> dict:
    if key_id:
        cfg["active_key"] = key_id
    if model:
        cfg["active_model"] = model
    save_config(cfg)
    return cfg


def reset_ledger() -> None:
    """清空账本（等价于"重新开始记账"）。"""
    save_json(os.path.join(HERE, "ledger.json"), [])


def _selftest() -> int:
    bad = 0

    def chk(name, cond, extra=""):
        nonlocal bad
        if not cond:
            print(f"FAIL {name} {extra}")
            bad += 1

    models = load_models()
    m = [x for x in models if x["id"] == "deepseek-v4-flash"][0]
    p = price_usage(m, {"input": 1_000_000, "output": 0, "reasoning": 0, "cache_hit": 0})
    chk("百万输入=输入单价", abs(p["in"] - m["input"]) < 1e-6, p)

    p = price_usage(m, {"input": 0, "output": 1_000_000, "reasoning": 0, "cache_hit": 0})
    chk("百万输出=输出单价", abs(p["out"] - m["output"]) < 1e-6, p)

    # 缓存应该比不缓存便宜
    a = price_usage(m, {"input": 1000, "cache_hit": 0})
    b = price_usage(m, {"input": 1000, "cache_hit": 1000})
    chk("命中缓存更便宜", b["total"] < a["total"], f"{a} vs {b}")

    # 思考按输出价计
    t = price_usage(m, {"output": 0, "reasoning": 1_000_000})
    chk("百万思考=输出单价", abs(t["think"] - m["output"]) < 1e-6, t)

    # 合计 = 各分项之和
    z = price_usage(m, {"input": 500, "output": 300, "reasoning": 200, "cache_hit": 100})
    chk("分项相加等于总计",
        abs(z["total"] - (z["in"] + z["out"] + z["cache"] + z["think"])) < 1e-6, z)

    cfg = {"total_quota": 100.0, "active_model": "deepseek-v4-flash", "active_key": "k1",
           "keys": [{"id": "k1", "label": "A", "quota": 60.0, "model": "deepseek-v4-flash"},
                    {"id": "k2", "label": "B", "quota": 40.0, "model": "deepseek-v4-pro"}]}
    ledger = [
        {"key_id": "k1", "input": 1_000_000, "output": 0, "reasoning": 0, "cache_hit": 0},
        {"key_id": "k2", "input": 0, "output": 1_000_000, "reasoning": 0, "cache_hit": 0},
    ]
    bill = compute_ledger_bill(ledger, cfg, models)
    # k1: flash 输入 0.5 元 ; k2: pro 输出 8 元
    chk("按 key 归集", abs(bill["keys"][0]["used"] - 0.5) < 1e-6, bill["keys"][0])
    chk("按 key 归集2", abs(bill["keys"][1]["used"] - 8.0) < 1e-6, bill["keys"][1])
    chk("总额", abs(bill["total_spent"] - 8.5) < 1e-6, bill["total_spent"])
    chk("余额", abs(bill["remaining"] - 91.5) < 1e-6, bill["remaining"])
    chk("剩余额度", abs(bill["keys"][1]["remaining"] - 32.0) < 1e-6, bill["keys"][1])

    if not bad:
        print("OK wcbilling 自测全部通过")
        print(f"   模型广场 {len(models)} 个模型，最贵 "
              f"{max(models, key=lambda x: x['output'])['id']}")
    return bad


if __name__ == "__main__":
    raise SystemExit(1 if _selftest() else 0)
