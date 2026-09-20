#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wcserve.py — 「聊天即消费」仪表盘服务端

把微信监听数据物化成 token 与金额，并提供：
  * 微信风格聊天界面（谁发来的、我发出去的、每条花了多少钱）
  * DeepSeek 用量信息风格的计费页（额度、消费、按 key / 按模型分布）
  * 模型广场（选模型、改价、自定义模型）

只监听 127.0.0.1，纯本地，不对外暴露。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 这个模块原来在仓库根目录，被移到 05-insight/consume/ 之后，
#    `import wcstat / wctokens` 就找不到了（它们在根目录）。
#    所以要把根目录也加进搜索路径。ROOT 是 05-insight/consume → 上两级。
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wcbilling as billing  # noqa: E402
import wcstat  # noqa: E402
import wcdraft  # noqa: E402
from wctokens import estimate_message  # noqa: E402

WEB_DIR = os.path.join(HERE, "web")
LEDGER_FILE = os.path.join(HERE, "ledger.json")
CACHE_FILE = os.path.join(HERE, "flow_cache.json")
KEEP_PER_CHAT = 500          # 每个会话界面保留多少条
LEDGER_KEEP = 200000         # 账本上限，防止无限增长
SYNC_INTERVAL = 20           # 秒


# ==========================================================================
# 存储：缓存 + 账本
# ==========================================================================

class Store:
    """线程安全的数据存储。

    账本是 append-only 的：每条消息一行 token 用量。
    金额不入库，永远由「账本 × 当时模型价」实时算，保证可重算、不漂移。
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.chats = {}          # wxid -> {name, msgs: [最近N条], ...}
        self.totals = {}         # wxid -> 全量聚合（不受显示上限影响）
        self.ledger = []
        self.cursor = {}         # table -> last_local_id
        self.hours = {}          # "YYYY-MM-DDTHH" -> {in, out, think, n}
        self.daily = {}          # "MM-DD" -> {in, out, think, n}  按天累积（图表用）
        self.self_info = {}
        self.last_sync = None
        self.last_error = None
        self.db_dir = None
        self.started = time.time()
        self.load()

    # ---------------- 持久化 ----------------

    def load(self) -> None:
        cache = billing.load_json(CACHE_FILE, {})
        self.chats = cache.get("chats", {})
        self.totals = cache.get("totals", {})
        self.ledger = billing.load_json(LEDGER_FILE, [])
        st = billing.load_json(wcstat.STATE_FILE, {})
        if st.get("_wccursor"):
            self.cursor = st["_wccursor"]
        self.self_info = cache.get("self", {})
        # 小时/按天分布优先从账本重建（账本含全量，比只留 500 条的 msgs 准）
        if self.ledger:
            for e in self.ledger:
                lt = time.localtime(e["t"])
                h = time.strftime("%Y-%m-%dT%H", lt)
                b = self.hours.setdefault(h, {"in": 0.0, "out": 0.0, "think": 0.0, "n": 0})
                b["in"] += e.get("input", 0.0)
                b["out"] += e.get("output", 0.0)
                b["think"] += e.get("reasoning", 0.0)
                b["n"] += 1
                d = time.strftime("%m-%d", lt)
                dd = self.daily.setdefault(d, {"in": 0.0, "out": 0.0, "think": 0.0, "n": 0})
                dd["in"] += e.get("input", 0.0)
                dd["out"] += e.get("output", 0.0)
                dd["think"] += e.get("reasoning", 0.0)
                dd["n"] += 1
        # 老版本缓存没有 totals：从 msgs 兜底重建，避免界面显示 0
        for wxid, c in self.chats.items():
            self.totals.setdefault(wxid, self._blank_total(c.get("name") or wxid))
            t = self.totals[wxid]
            if not t["n"] and c.get("msgs"):
                for m in c["msgs"]:
                    self._bump(t, m)
                    t["n"] += 1

    @staticmethod
    def _blank_total(name):
        return {"name": name, "n": 0, "n_in": 0, "n_out": 0, "input": 0.0,
                "output": 0.0, "reasoning": 0.0, "cache_hit": 0.0, "total": 0.0,
                "first": None, "last": None,
                "by_kind": {}}

    @staticmethod
    def _bump(t, m):
        for k in ("input", "output", "reasoning", "cache_hit", "total"):
            t[k] += m.get(k, 0.0)
        t["n_in" if m.get("dir") == "in" else "n_out"] += 1
        kk = m.get("kind", "text")
        t["by_kind"][kk] = t["by_kind"].get(kk, 0) + 1

    def save(self) -> None:
        billing.save_json(CACHE_FILE, {"chats": self.chats, "totals": self.totals,
                                       "self": self.self_info})
        billing.save_json(LEDGER_FILE, self.ledger[-LEDGER_KEEP:])
        st = billing.load_json(wcstat.STATE_FILE, {})
        st["_wccursor"] = self.cursor
        billing.save_json(wcstat.STATE_FILE, st)

    # ---------------- 写入 ----------------

    def add(self, rec: dict) -> None:
        """rec 是一条已经算好 token 的消息。"""
        with self.lock:
            wxid = rec["wxid"]
            chat = self.chats.setdefault(wxid, {
                "wxid": wxid, "name": rec.get("name") or wxid, "is_group": False,
                "msgs": [], "first": rec["t"], "last": rec["t"],
            })
            # 去重：同一会话同一 local_id 只记一次（防止解密镜像重复灌入）
            key = rec["key"]
            if any(m.get("key") == key for m in chat["msgs"][-120:]):
                return
            chat["msgs"].append(rec)
            if len(chat["msgs"]) > KEEP_PER_CHAT:
                chat["msgs"] = chat["msgs"][-KEEP_PER_CHAT:]
            chat["last"] = max(chat["last"], rec["t"])
            chat["first"] = min(chat["first"], rec["t"])
            if rec.get("name"):
                chat["name"] = rec["name"]

            # 全量聚合：不受 KEEP_PER_CHAT 影响，保证总计准确
            t = self.totals.setdefault(wxid, self._blank_total(chat["name"]))
            if rec.get("name"):
                t["name"] = rec["name"]
            self._bump(t, rec)
            t["n"] += 1
            t["first"] = rec["t"] if t["first"] is None else min(t["first"], rec["t"])
            t["last"] = rec["t"] if t["last"] is None else max(t["last"], rec["t"])

            self.ledger.append({
                "id": rec["key"], "wxid": wxid, "t": rec["t"], "dir": rec["dir"],
                # model=None 表示"跟随该会话分配的 Key"，改 Key 时历史会自动重算
                "model": rec.get("model") if rec.get("pin_model") else None,
                "input": rec.get("input", 0.0), "output": rec.get("output", 0.0),
                "reasoning": rec.get("reasoning", 0.0),
                "cache_hit": rec.get("cache_hit", 0.0),
                "kind": rec.get("kind"),
            })
            if len(self.ledger) > LEDGER_KEEP:
                self.ledger = self.ledger[-LEDGER_KEEP:]
            h = time.strftime("%Y-%m-%dT%H", time.localtime(rec["t"]))
            b = self.hours.setdefault(h, {"in": 0.0, "out": 0.0, "think": 0.0, "n": 0})
            b["in"] += rec.get("input", 0.0)
            b["out"] += rec.get("output", 0.0)
            b["think"] += rec.get("reasoning", 0.0)
            b["n"] += 1
            # 按天累积：图表直接用这个，避免每次请求把 10 万条账本重算一遍
            d = time.strftime("%m-%d", time.localtime(rec["t"]))
            dd = self.daily.setdefault(d, {"in": 0.0, "out": 0.0, "think": 0.0, "n": 0})
            dd["in"] += rec.get("input", 0.0)
            dd["out"] += rec.get("output", 0.0)
            dd["think"] += rec.get("reasoning", 0.0)
            dd["n"] += 1

    def to_ledger(self):
        with self.lock:
            return list(self.ledger)

    def chats_snapshot(self):
        with self.lock:
            return {k: v for k, v in self.chats.items()}


STORE = Store()


# ==========================================================================
# 同步：微信库 -> token -> 账本
# ==========================================================================

def _record(wxid, name, t, direction, local_id, table, kind, text, model_id, is_group=False,
            pin_model=False):
    """把一条消息变成账本记录。

    pin_model=False（默认）：不把模型写死进账本，金额在查询时按「该会话分配的 Key」
    解析——这样以后给某个会话塞 Key，它的历史会一起重算。
    """
    est = estimate_message(text, "in" if direction == "in" else "out")
    return {
        "key": f"{table}:{local_id}",
        "wxid": wxid, "name": name, "t": int(t), "dir": direction,
        "kind": kind, "text": text[:600],
        "input": est["input"], "output": est["output"],
        "reasoning": est["reasoning"], "cache_hit": est["cache_hit"],
        "cache_miss": est["cache_miss"], "total": est["total"],
        "model": model_id if pin_model else None, "pin_model": pin_model,
        "is_group": is_group,
        "local_id": local_id,
    }


def sync_once(db_dir: str | None = None, rebuild: bool = False,
              since_days: int = 0) -> dict:
    """从微信库增量取消息，转成 token，写进账本。

    since_days > 0 时只回填最近 N 天（首次启动用，避免把几年的历史全灌进内存）；
    之后的增量同步不受它影响（游标已经推进到最新）。
    """
    global STORE
    if rebuild:
        with STORE.lock:
            STORE.chats = {}
            STORE.totals = {}
            STORE.ledger = []
            STORE.hours = {}
            STORE.daily = {}
            STORE.cursor = {}
    try:
        if db_dir is None:
            db_dir = wcstat.find_db_dir()
        STORE.db_dir = db_dir
        cfg = billing.load_config()
        model_id = cfg.get("active_model") or billing.BUILTIN_MODELS[0]["id"]
        cursor = dict(STORE.cursor)
        self_info = None
        n = 0
        # 会话显示名：群名 / 联系人备注；成员昵称用于把群消息里的 wxid 前缀换成人话
        try:
            names = wcstat.session_names(db_dir)
        except Exception:
            names = {}
        try:
            members = wcstat.member_names(db_dir)
        except Exception:
            members = {}
        groups = {}
        since_time = int(time.time() - since_days * 86400) if since_days else 0
        for (table, sess, direction, local_id, ctime, mtype, dec,
             is_group) in wcstat.iter_new_messages(db_dir, since_map=cursor,
                                                   since_time=since_time):
            cursor[table] = local_id
            if self_info is None:
                self_info = {"wxid": wcstat.resolve_my_wxid(db_dir)}
            groups[sess] = is_group
            if dec["kind"] == "system":
                # 系统消息不进账单，但推进游标（避免每次都重新扫）
                continue
            if not dec["countable"] or not dec["text"]:
                continue
            # 群消息原文带 `发送者id:\n正文`，换成人话
            sender_name, body = wcstat.split_sender(dec["text"], is_group, members)
            text = body or dec["text"]
            rec = _record(sess, names.get(sess), ctime, direction, local_id, table,
                          dec["kind"], text, model_id, is_group=is_group)
            rec["sender"] = sender_name or None
            STORE.add(rec)
            n += 1
        with STORE.lock:
            for sess, g in groups.items():
                c = STORE.chats.get(sess)
                if c:
                    c["is_group"] = g
            for sess, c in STORE.chats.items():
                if names.get(sess):
                    c["name"] = names[sess]
                t = STORE.totals.get(sess)
                if t and names.get(sess):
                    t["name"] = names[sess]
        with STORE.lock:
            STORE.cursor = cursor
            if self_info:
                STORE.self_info = self_info
            STORE.last_sync = time.strftime("%Y-%m-%d %H:%M:%S")
            STORE.last_error = None
        # 兜底：stats.json 里 wcstat 那次统计留下的名字
        stats = billing.load_json(wcstat.STATS_FILE, {})
        people = stats.get("people", {})
        with STORE.lock:
            for wxid, chat in STORE.chats.items():
                if chat.get("name") in (None, "", wxid):
                    p = people.get(wxid)
                    if p and p.get("name"):
                        chat["name"] = p["name"]
        STORE.save()
        return {"ok": True, "added": n, "total_ledger": len(STORE.ledger),
                "at": STORE.last_sync}
    except Exception as e:  # 不因为一次同步失败就让服务挂掉
        with STORE.lock:
            STORE.last_error = f"{type(e).__name__}: {e}"
        return {"ok": False, "error": STORE.last_error, "added": 0}


def sync_loop(stop: threading.Event, interval: int, db_dir=None) -> None:
    while not stop.is_set():
        r = sync_once(db_dir)
        if r.get("added"):
            bus.publish({"type": "sync", **r})
        stop.wait(interval)


# ==========================================================================
# 事件总线（SSE）
# ==========================================================================

class Bus:
    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()
        self.recent = []

    def publish(self, payload: dict) -> None:
        line = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        with self.lock:
            self.recent.append(line)
            self.recent = self.recent[-50:]
            dead = []
            for q in self.clients:
                try:
                    q.append(line)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.clients.remove(q)

    def subscribe(self):
        q = list(self.recent[-10:])
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)


bus = Bus()


# ==========================================================================
# 视图组装
# ==========================================================================

def clear_demo_data() -> dict:
    """只清掉模拟产生的演示会话（wxid 以 demo_ 开头），真实聊天数据保留。"""
    removed_chats = removed_entries = 0
    with STORE.lock:
        for wxid in [w for w in list(STORE.chats) if w.startswith("demo_")]:
            STORE.chats.pop(wxid, None)
            STORE.totals.pop(wxid, None)
            removed_chats += 1
        before = len(STORE.ledger)
        STORE.ledger = [e for e in STORE.ledger if not str(e.get("wxid", "")).startswith("demo_")]
        removed_entries = before - len(STORE.ledger)
        # 重算小时/按天聚合（它们是从账本推出来的）
        STORE.hours = {}
        STORE.daily = {}
        for e in STORE.ledger:
            lt = time.localtime(e["t"])
            h = time.strftime("%Y-%m-%dT%H", lt)
            b = STORE.hours.setdefault(h, {"in": 0.0, "out": 0.0, "think": 0.0, "n": 0})
            b["in"] += e.get("input", 0.0); b["out"] += e.get("output", 0.0)
            b["think"] += e.get("reasoning", 0.0); b["n"] += 1
            d = time.strftime("%m-%d", lt)
            dd = STORE.daily.setdefault(d, {"in": 0.0, "out": 0.0, "think": 0.0, "n": 0})
            dd["in"] += e.get("input", 0.0); dd["out"] += e.get("output", 0.0)
            dd["think"] += e.get("reasoning", 0.0); dd["n"] += 1
    STORE.save()
    bus.publish({"type": "demo_cleared"})
    return {"ok": True, "removed_chats": removed_chats, "removed_entries": removed_entries}


def view_overview() -> dict:
    cfg = billing.load_config()
    models = billing.load_models()
    bill = billing.compute_ledger_bill(STORE.to_ledger(), cfg, models)
    active_key = next((k for k in bill["keys"] if k["id"] == cfg.get("active_key")), None)
    with STORE.lock:
        chats = STORE.chats
        totals = STORE.totals
        n_msgs = sum(t["n"] for t in totals.values())
        t_in = sum(t["input"] for t in totals.values())
        t_out = sum(t["output"] for t in totals.values())
        t_think = sum(t["reasoning"] for t in totals.values())
    return {
        "self": STORE.self_info,
        "billing": bill,
        "active_key": active_key,
        "active_model": cfg.get("active_model"),
        "model": billing.get_model(cfg.get("active_model") or ""),
        "tokens": {"input": round(t_in, 1), "output": round(t_out, 1),
                   "reasoning": round(t_think, 1),
                   "total": round(t_in + t_out + t_think, 1)},
        "chats": len([t for t in totals.values() if t["n"]]), "messages": n_msgs,
        "last_sync": STORE.last_sync, "last_error": STORE.last_error,
        "uptime": int(time.time() - STORE.started),
        "db_dir": STORE.db_dir,
        "price_now": {
            "input": billing.get_model(cfg.get("active_model") or "")["input"],
            "output": billing.get_model(cfg.get("active_model") or "")["output"],
            "cache": billing.get_model(cfg.get("active_model") or "").get("cache", 0),
        },
    }


def view_conversations() -> list:
    cfg = billing.load_config()
    models = billing.load_models()
    by_id = {m["id"]: m for m in models}
    global_model = by_id.get(cfg.get("active_model")) or models[0]
    pk = cfg.get("person_keys") or {}
    key_models = {k["id"]: by_id.get(k.get("model")) or global_model
                  for k in cfg.get("keys", [])}
    out = []
    with STORE.lock:
        for wxid, c in STORE.chats.items():
            t = STORE.totals.get(wxid)
            if not t or t["n"] == 0:
                continue
            # 每个人的花费按「他绑定的 Key 对应模型」算，没绑就用全局模型
            pkey = pk.get(wxid)
            pkey_obj = next((k for k in cfg.get("keys", []) if k["id"] == pkey), None)
            model = key_models.get(pkey, global_model)
            usage = {"input": t["input"], "output": t["output"],
                     "reasoning": t["reasoning"], "cache_hit": t["cache_hit"]}
            price = billing.price_usage(model, usage)
            msgs = c.get("msgs") or []
            last = msgs[-1] if msgs else {}
            out.append({
                "wxid": wxid, "name": c.get("name") or t.get("name") or wxid,
                "is_group": c.get("is_group", False),
                "n": t["n"],
                "tokens": {k: round(v, 1) for k, v in usage.items()},
                "cost": round(price["total"], 4),
                "cost_detail": {k: round(v, 4) for k, v in price.items()},
                "last_text": last.get("text", "")[:60],
                "last_t": last.get("t"), "last_dir": last.get("dir"),
                "first": t["first"], "last": t["last"],
                "n_in": t["n_in"], "n_out": t["n_out"],
                # 计费归属，给「塞 Key」界面用
                "billing": {
                    "key_id": pkey, "key_label": (pkey_obj or {}).get("label"),
                    "model_id": model["id"], "model_name": model["name"],
                    "source": "person" if pkey else "global",
                    "price": {"input": model["input"], "output": model["output"]},
                },
            })
    out.sort(key=lambda x: (-(x["last"] or 0)))
    return out


def view_history(wxid: str, limit: int = 200, before: int | None = None) -> dict:
    """某个会话的消息明细。金额按**该会话分配的 Key 对应模型**计算，
    没分配就用全局模型——所以给会话塞 Key 后，历史明细里的每条费用也会变。"""
    cfg = billing.load_config()
    models = billing.load_models()
    by_id = {m["id"]: m for m in models}
    pv = billing.person_bill_view(cfg, wxid, models)
    model = by_id.get(pv["model_id"]) or models[0]
    with STORE.lock:
        c = STORE.chats.get(wxid)
        if not c:
            return {"wxid": wxid, "name": wxid, "msgs": [], "cost": 0, "n": 0,
                    "person": pv, "model_id": model["id"], "model_name": model["name"]}
        msgs = c["msgs"]
        if before:
            msgs = [m for m in msgs if m["t"] < before]
        tail = msgs[-limit:]
        out = []
        for m in tail:
            price = billing.price_usage(model, m)
            out.append({**{k: m[k] for k in ("key", "t", "dir", "kind", "text", "input",
                                             "output", "reasoning", "cache_hit", "model",
                                             "sender")
                           if k in m},
                        "cost": round(price["total"], 6),
                        "cost_detail": {k: round(v, 6) for k, v in price.items()}})
        total_cost = sum(billing.price_usage(model, m)["total"] for m in msgs)
        return {"wxid": wxid, "name": c.get("name") or wxid, "is_group": c.get("is_group"),
                "msgs": out, "cost": round(total_cost, 4), "n": len(msgs),
                "has_more": len(msgs) > len(tail),
                "person": pv, "model_id": model["id"], "model_name": model["name"]}


def view_timeseries(days: int = 14) -> list:
    """按天汇总 token 与费用（供图表）。

    直接用写入时累积好的按天数据，不再遍历账本逐条算价
    （那样 10 万条要 700ms，而图表只是要 30 个点）。
    """
    cfg = billing.load_config()
    models = billing.load_models()
    by_id = {m["id"]: m for m in models}
    model = by_id.get(cfg.get("active_model")) or models[0]
    now = time.time()
    with STORE.lock:
        daily = dict(STORE.daily)
    out = []
    for i in range(days - 1, -1, -1):
        key = time.strftime("%m-%d", time.localtime(now - i * 86400))
        b = daily.get(key) or {"in": 0.0, "out": 0.0, "think": 0.0, "n": 0}
        usage = {"input": b["in"], "output": b["out"], "reasoning": b["think"],
                 "cache_hit": b["in"] * 0.65}
        price = billing.price_usage(model, usage)
        out.append({"day": key, "input": round(b["in"], 1), "output": round(b["out"], 1),
                    "reasoning": round(b["think"], 1), "n": b["n"],
                    "cost": round(price["total"], 6)})
    return out


def view_breakdown() -> dict:
    """按人 / 按种类 / 输入输出比的分布。每个人的金额按他自己的计费归属算。"""
    cfg = billing.load_config()
    models = billing.load_models()
    by_id = {m["id"]: m for m in models}
    global_model = by_id.get(cfg.get("active_model")) or models[0]
    pk = cfg.get("person_keys") or {}
    key_models = {k["id"]: by_id.get(k.get("model")) or global_model
                  for k in cfg.get("keys", [])}
    by_kind, by_person = {}, []
    t_in = t_out = t_think = 0.0
    with STORE.lock:
        for wxid, c in STORE.chats.items():
            t = STORE.totals.get(wxid)
            if not t or t["n"] == 0:
                continue
            u = {"input": t["input"], "output": t["output"],
                 "reasoning": t["reasoning"], "cache_hit": t["cache_hit"]}
            for kk, cnt in (t.get("by_kind") or {}).items():
                b = by_kind.setdefault(kk, {"kind": kk, "n": 0, "tokens": 0.0})
                b["n"] += cnt
            t_in += u["input"]
            t_out += u["output"]
            t_think += u["reasoning"]
            model = key_models.get(pk.get(wxid), global_model)
            price = billing.price_usage(model, u)
            by_person.append({
                "wxid": wxid, "name": c.get("name") or t.get("name") or wxid,
                "tokens": round(sum(u.values()), 1), "cost": round(price["total"], 4),
                "input": round(u["input"], 1), "output": round(u["output"], 1),
                "reasoning": round(u["reasoning"], 1),
                "n": t["n"], "model_id": model["id"],
            })
    # 种类 token 量按该种类的消息条数比例分摊（totals 只存了条数）
    total_msgs = sum(v["n"] for v in by_kind.values()) or 1
    grand_tokens = t_in + t_out + t_think
    for v in by_kind.values():
        v["tokens"] = round(grand_tokens * v["n"] / total_msgs, 1)
    by_person.sort(key=lambda x: -x["cost"])
    ratio = (t_in / t_out) if t_out else 0
    return {
        "by_person": by_person[:50],
        "by_kind": sorted([{**v, "tokens": round(v["tokens"], 1)}
                           for v in by_kind.values()], key=lambda x: -x["n"]),
        "ratio": {"input": round(t_in, 1), "output": round(t_out, 1),
                  "reasoning": round(t_think, 1),
                  "in_out": round(ratio, 3)},
    }


def view_hours(hours: int = 48) -> list:
    now = time.time()
    out = []
    with STORE.lock:
        for i in range(hours - 1, -1, -1):
            h = time.strftime("%Y-%m-%dT%H", time.localtime(now - i * 3600))
            b = STORE.hours.get(h)
            out.append({"h": h,
                        "label": time.strftime("%H:00", time.localtime(now - i * 3600)),
                        "input": round(b["in"], 1) if b else 0,
                        "output": round(b["out"], 1) if b else 0,
                        "reasoning": round(b["think"], 1) if b else 0,
                        "n": b["n"] if b else 0})
    return out


# ==========================================================================
# 模拟器：20 轮用户使用模拟
# ==========================================================================

PERSONAS = [
    ("阿澈", ["在吗", "帮我看下这个报错咋回事", "已经好了，谢谢！", "明天几点见面", "嗯嗯"]),
    ("小茶", ["哈哈哈哈哈哈", "你今天怎么这么安静", "我在图书馆", "好的呢～", "晚安😴"]),
    ("老周", ["合同我看了，第三条款要改", "https://example.com/doc/123", "你先把版本发我", "收到"]),
    ("Miho", ["おはよう！", "今天下雨了☔", "你看那个电影了吗", "好想看", "😀😀😀"]),
    ("产品群", ["@所有人 周会改到周四", "好的", "收到", "我这边没问题", "那就这样"]),
]

DEMO_IN = [
    "在吗", "你现在方便吗", "这个报错怎么解决", "Traceback (most recent call last): KeyError 'user_id'",
    "哈哈哈哈哈哈", "你看这个 https://example.com/a", "好累啊今天", "明天几点？",
    "我先睡了", "嗯", "😀😀😀", "刚刚那个文件发我一下", "谢啦", "好滴",
    "我觉得可以", "那算了", "你在干嘛呢", "别熬夜了", "醒了吗", "早安",
]
DEMO_OUT = [
    "在的", "我看下", "这个是因为缓存没命中，加个判断就行", "好的", "嗯嗯",
    "哈哈哈哈", "收到", "晚点发你", "早点休息", "晚安", "我在忙，稍后回你",
    "行", "那就这样定了", "别闹", "好", "我看看再说", "😴", "嗯好",
]


def simulate(rounds: int = 20, verbose: bool = True) -> dict:
    """跑 rounds 轮"用户使用模拟"，生成拟真对话与账单，用于验收界面。

    注意：演示会话的 wxid 必须**稳定**（用 md5 而不是 hash()）。
    Python 的字符串 hash 每次进程启动都带随机盐，用它生成的 id 每次都不一样，
    跑几次模拟就会攒出一堆重名会话（踩过这个坑）。
    """
    cfg = billing.load_config()
    model_id = cfg.get("active_model")
    total_added = 0
    log = []
    base_t = time.time() - rounds * 900
    for r in range(rounds):
        name, pool = PERSONAS[r % len(PERSONAS)]
        wxid = "demo_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
        # 对方发来 1~2 条
        for _ in range(random.choice([1, 1, 2])):
            t = base_t + r * 900 + random.randint(0, 300)
            kind = random.choices(["text", "sticker", "image", "quote"],
                                  weights=[8, 1, 1, 1])[0]
            if kind == "text":
                text = random.choice(DEMO_IN)
            elif kind == "sticker":
                text = "[表情 %s]" % random.choice(["偷笑", "a4bd3c83", "狗头"])
            elif kind == "image":
                text = "[图片]"
            else:
                text = "[引用] " + random.choice(DEMO_IN)[:12]
            rec = _record(wxid, name, t, "in", r * 100 + random.randint(1, 50),
                          "sim", kind, text, model_id)
            STORE.add(rec)
            total_added += 1
        # 我回复 1 条
        t = base_t + r * 900 + random.randint(300, 600)
        out_text = random.choice(DEMO_OUT)
        rec = _record(wxid, name, t, "out", r * 100 + random.randint(51, 99),
                      "sim", "text", out_text, model_id)
        STORE.add(rec)
        total_added += 1
        log.append({"round": r + 1, "who": name, "in": len(pool)})
    STORE.save()
    if verbose:
        bill = billing.compute_ledger_bill(STORE.to_ledger(), cfg, billing.load_models())
        print(f"[sim] {rounds} 轮模拟完成，写入 {total_added} 条消息")
        print(f"[sim] 账本 {bill['ledger_entries']} 条，累计消费 ¥{bill['total_spent']:.4f}"
              f" / 额度 ¥{bill['total_quota']:.2f}")
    bus.publish({"type": "sim", "added": total_added})
    return {"added": total_added, "rounds": rounds, "log": log[-5:]}


# ==========================================================================
# HTTP
# ==========================================================================

CTYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".js": "application/javascript; charset=utf-8", ".json": "application/json",
          ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
          ".woff2": "font/woff2", ".map": "application/json"}


class QuietServer(ThreadingHTTPServer):
    """把「浏览器掐断连接」这类正常现象产生的 traceback 静默掉。

    浏览器刷新/关页面会直接断 TCP（SSE 尤其频繁），socketserver 默认在
    process_request_thread 里 print 整段 ConnectionResetError 堆栈，
    日志会被刷满。这不是错误，覆盖掉即可。
    真正的请求异常仍然照常抛出（只吞连接层的三个错误）。
    """

    daemon_threads = True

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "wccost/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("WCSERVE_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------- 工具 -------------

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path: str):
        rel = path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            self._json({"error": "not found", "path": path}, 404)
            return
        ctype = CTYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------- 路由 -------------

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)

        if p == "/api/stream":
            return self._sse()
        if p == "/api/overview":
            return self._json(view_overview())
        if p == "/api/conversations":
            return self._json(view_conversations())
        if p == "/api/history":
            return self._json(view_history(q.get("wxid", [""])[0],
                                          int(q.get("limit", ["200"])[0])))
        if p == "/api/timeseries":
            return self._json(view_timeseries(int(q.get("days", ["14"])[0])))
        if p == "/api/breakdown":
            return self._json(view_breakdown())
        if p == "/api/person":
            wxid = q.get("wxid", [""])[0]
            cfg = billing.load_config()
            return self._json(billing.person_bill_view(cfg, wxid, billing.load_models()))
        if p == "/api/hours":
            return self._json(view_hours(int(q.get("hours", ["48"])[0])))
        if p == "/api/billing":
            return self._json(self._billing())
        if p == "/api/models":
            return self._json({"models": billing.load_models()})
        if p == "/api/draft/config":
            return self._json(wcdraft.load_cfg())
        if p == "/api/health":
            return self._json({"ok": True, "last_sync": STORE.last_sync,
                               "chats": len(STORE.chats)})
        if p.startswith("/api/"):
            return self._json({"error": "unknown endpoint"}, 404)
        return self._static(p)

    def do_POST(self):
        u = urlparse(self.path)
        p, body = u.path, self._body()

        if p == "/api/sync":
            return self._json(sync_once())
        if p == "/api/simulate":
            return self._json(simulate(int(body.get("rounds", 20)), verbose=False))
        if p == "/api/config/quota":
            cfg = billing.load_config()
            billing.set_quota(cfg, body.get("total_quota"))
            return self._json(self._billing())
        if p == "/api/keys":
            cfg = billing.load_config()
            r = billing.add_key(cfg, body.get("label", ""), body.get("quota", 0),
                                body.get("masked", ""), body.get("model", ""),
                                body.get("note", ""))
            return self._json({"ok": True, **r, "billing": self._billing()})
        if p == "/api/keys/update":
            cfg = billing.load_config()
            k = billing.update_key(cfg, body.get("id", ""), body)
            return self._json({"ok": bool(k), "key": k, "billing": self._billing()})
        if p == "/api/keys/delete":
            cfg = billing.load_config()
            ok = billing.delete_key(cfg, body.get("id", ""))
            return self._json({"ok": ok, "billing": self._billing()})
        if p == "/api/active":
            cfg = billing.load_config()
            billing.set_active(cfg, body.get("key_id"), body.get("model"))
            return self._json({"ok": True, "overview": view_overview()})
        if p == "/api/person/assign":
            cfg = billing.load_config()
            wxid = body.get("wxid") or ""
            if not wxid:
                return self._json({"error": "缺少 wxid"}, 400)
            try:
                billing.assign_person_key(cfg, wxid, body.get("key_id") or None)
            except KeyError as e:
                return self._json({"error": str(e)}, 400)
            # 分配变了 → 该会话全部历史按新模型重算，顺带刷新缓存
            return self._json({"ok": True, "person": view_history(wxid)["person"],
                               "billing": self._billing()})
        if p == "/api/models/custom":
            m = billing.save_custom_model(body.get("model", {}))
            return self._json({"ok": True, "model": m, "models": billing.load_models()})
        if p == "/api/models/price":
            m = billing.update_model_price(body.get("id", ""), body.get("price", {}))
            return self._json({"ok": True, "model": m, "models": billing.load_models()})
        if p == "/api/models/delete":
            ok = billing.delete_custom_model(body.get("id", ""))
            return self._json({"ok": ok, "models": billing.load_models()})
        if p == "/api/ledger/reset":
            billing.reset_ledger()
            with STORE.lock:
                STORE.ledger = []
                STORE.chats = {}
                STORE.totals = {}
                STORE.hours = {}
                STORE.daily = {}
                STORE.cursor = {}
            STORE.save()
            return self._json({"ok": True})
        if p == "/api/demo/clear":
            return self._json(clear_demo_data())
        if p == "/api/draft":
            return self._json(self._draft(body))
        if p == "/api/draft/config":
            cfg = wcdraft.load_cfg()
            cfg.update({k: v for k, v in body.items() if k in wcdraft.DEFAULT_CFG})
            return self._json({"ok": True, "config": wcdraft.save_cfg(cfg)})
        return self._json({"error": "unknown endpoint"}, 404)

    def _draft(self, body: dict) -> dict:
        """生成候选回复。**只生成文字，不发送**——发送永远由人来做。"""
        wxid = body.get("wxid") or ""
        topic = (body.get("topic") or "").strip()[:200]
        if not wxid:
            return {"error": "缺少 wxid"}
        h = view_history(wxid, limit=30)
        if not h.get("msgs"):
            return {"error": "这个会话还没有消息，没法拟回复"}
        ctx = [{"dir": m["dir"], "sender": m.get("sender"), "text": m.get("text", "")}
               for m in h["msgs"]]
        r = wcdraft.generate(h.get("name") or wxid, ctx, wxid, topic=topic)
        r["chat"] = h.get("name")
        return r

    def _billing(self) -> dict:
        cfg = billing.load_config()
        bill = billing.compute_ledger_bill(STORE.to_ledger(), cfg, billing.load_models())
        bill["config"] = {"active_key": cfg.get("active_key"),
                          "active_model": cfg.get("active_model"),
                          "currency": cfg.get("currency", "CNY"),
                          "updated": cfg.get("updated"),
                          "alerts": cfg.get("alerts", {})}
        bill["models"] = billing.load_models()
        return bill

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = bus.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            last = 0
            while True:
                if len(q) > last:
                    for line in q[last:]:
                        self.wfile.write(line.encode("utf-8"))
                    last = len(q)
                    self.wfile.flush()
                else:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                time.sleep(1.5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.unsubscribe(q)


def main():
    ap = argparse.ArgumentParser(description="微信聊天成本仪表盘")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--interval", type=int, default=SYNC_INTERVAL)
    ap.add_argument("--no-sync", action="store_true", help="不连微信，只用已有账本")
    ap.add_argument("--rebuild", action="store_true", help="启动前清空账本重建")
    ap.add_argument("--simulate", type=int, default=0, help="先跑 N 轮模拟再启动")
    ap.add_argument("--since-days", type=int, default=0,
                    help="首次回填只取最近 N 天（0=全部，默认 0）")
    ap.add_argument("--db-dir", default=None)
    ap.add_argument("--clear-demo", action="store_true",
                    help="启动前清掉模拟产生的演示会话（demo_ 开头），保留真实数据")
    args = ap.parse_args()

    if args.clear_demo:
        r = clear_demo_data()
        print(f"[init] 已清理演示数据：{r['removed_chats']} 个会话 / {r['removed_entries']} 条记录")
    if args.rebuild:
        sync_once(args.db_dir, rebuild=True, since_days=args.since_days)
    if args.simulate:
        simulate(args.simulate)
    if not args.no_sync and not args.rebuild:
        r = sync_once(args.db_dir, since_days=args.since_days)
        print(f"[init] 首次同步: {r}")

    stop = threading.Event()
    if not args.no_sync:
        threading.Thread(target=sync_loop, args=(stop, args.interval, args.db_dir),
                         daemon=True).start()

    srv = QuietServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print("=" * 62)
    print("  聊天即消费 · 仪表盘")
    print(f"  {url}")
    print(f"  账本 {len(STORE.ledger)} 条 / 会话 {len(STORE.chats)} 个"
          f" / 同步间隔 {args.interval}s" + ("  [已关闭同步]" if args.no_sync else ""))
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[bye]")
    finally:
        stop.set()
        srv.server_close()


if __name__ == "__main__":
    main()
