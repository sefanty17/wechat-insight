# -*- coding: utf-8 -*-
"""
wcstore.py — 把微信消息全量导入本地 SQLite，并提供检索

为什么要自己建一个库，而不是每次去查解密后的微信库：
  · 微信库每次都要重新解密（3~8 秒），而且是只读镜像，不能加索引
  · 我们需要在正文上做检索、加自己的标注（情绪/待办/人设标签）
  · 需要一个稳定的、可以反复分析的数据底座

设计要点：
  · **消息按 (chat_id, local_id) 唯一**，重复导入不会产生重复行
  · 存 token 数（复用已标定过的 wctokens），这样"聊天即消费"的统计也能直接用
  · 保留 direction（in/out）**且方向判定经过验证**（见 wcmessages.py 的说明）
  · 用 LIKE 子串检索（实测 10 万条 50~200ms），不依赖 jieba 或向量模型
  · 支持增量：用 meta 表记每张微信消息表已处理到的 local_id
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from wcmessages import WeChatData  # noqa: E402

DEFAULT_DB = os.path.join(HERE, "chat.db")

SCHEMA = """
create table if not exists meta (
  k text primary key,
  v text
);

create table if not exists chats (
  chat_id     text primary key,   -- 会话 id（wxid / xxx@chatroom）
  name        text,               -- 显示名（群名/备注）
  is_group    integer default 0,
  first_t     integer,
  last_t      integer,
  n_total     integer default 0,
  n_in        integer default 0,
  n_out       integer default 0
);

create table if not exists messages (
  id        integer primary key autoincrement,
  chat_id   text not null,
  local_id  integer not null,
  t         integer not null,       -- unix 秒
  day       text,                   -- YYYY-MM-DD，便于按天聚合
  direction text not null,          -- in | out
  sender    text,                   -- 发送者 wxid（可能为空=群里未识别）
  sender_name text,
  kind      text,                   -- text/sticker/image/quote/voice/...
  text      text,
  tok       real default 0,         -- 该消息的 token 估算
  unique(chat_id, local_id)
);

create index if not exists idx_msg_chat_t  on messages(chat_id, t);
create index if not exists idx_msg_t       on messages(t);
create index if not exists idx_msg_dir     on messages(direction);
create index if not exists idx_msg_kind    on messages(kind);
create index if not exists idx_msg_sender  on messages(sender);
"""


class Store:
    def __init__(self, path: str = DEFAULT_DB, verbose: bool = True):
        self.path = path
        self.verbose = verbose
        # check_same_thread=False：HTTP 服务里每个请求跑在不同线程，
        # 而 Store 是进程级共享的。默认设置会直接抛
        # "SQLite objects created in a thread can only be used in that same thread"。
        # sqlite3 模块本身是线程安全的（内部串行化），单条语句不会互相破坏；
        # 需要"读-改-写"原子性的地方由调用方加锁（见 wcask.py 的并发保护）。
        self.con = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.con.execute("pragma journal_mode=WAL")
        self.con.execute("pragma synchronous=NORMAL")
        self.con.executescript(SCHEMA)
        self.con.commit()

    def _log(self, m):
        if self.verbose:
            print(m, flush=True)

    # ---------------- meta ----------------

    def get_meta(self, k, default=None):
        r = self.con.execute("select v from meta where k=?", (k,)).fetchone()
        return r[0] if r else default

    def set_meta(self, k, v):
        self.con.execute("insert into meta(k,v) values(?,?) "
                         "on conflict(k) do update set v=excluded.v", (k, str(v)))
        self.con.commit()

    # ---------------- 导入 ----------------

    def import_all(self, incremental: bool = True, limit: int = 0) -> dict:
        """从微信库导入消息。incremental=True 时只取上次之后的新消息。"""
        from wctokens import estimate_message

        d = WeChatData(verbose=self.verbose)
        since = {}
        if incremental:
            import json
            raw = self.get_meta("cursor")
            if raw:
                try:
                    since = json.loads(raw)
                except Exception:
                    since = {}

        t0 = time.time()
        n_new = n_seen = 0
        cur_chat = {}
        batch = []
        cursor = dict(since)

        def flush():
            nonlocal batch
            if not batch:
                return
            self.con.executemany(
                "insert or ignore into messages"
                "(chat_id,local_id,t,day,direction,sender,sender_name,kind,text,tok) "
                "values(?,?,?,?,?,?,?,?,?,?)", batch)
            self.con.commit()
            batch = []

        for m in d.iter_messages(since_local_id=since, batch_log=self.verbose):
            n_seen += 1
            cursor[m["table"]] = m["local_id"]
            tok = 0.0
            if m["countable"] and m["text"]:
                try:
                    est = estimate_message(m["text"], m["direction"])
                    tok = est.get("total", 0.0)
                except Exception:
                    tok = 0.0
            batch.append((
                m["sess"], m["local_id"], m["t"],
                time.strftime("%Y-%m-%d", time.localtime(m["t"])) if m["t"] else None,
                m["direction"], m["sender_wxid"], m["sender_name"],
                m["kind"], m["text"], tok))
            n_new += 1
            c = cur_chat.setdefault(m["sess"], {
                "name": m["chat_name"], "is_group": int(m["is_group"]),
                "first": m["t"], "last": m["t"], "n": 0, "nin": 0, "nout": 0})
            c["first"] = min(c["first"], m["t"]) if c["first"] else m["t"]
            c["last"] = max(c["last"], m["t"])
            c["n"] += 1
            c["nout" if m["direction"] == "out" else "nin"] += 1
            if len(batch) >= 5000:
                flush()
            if limit and n_new >= limit:
                break
        flush()

        # 会话汇总（累加到已有值上）
        for cid, c in cur_chat.items():
            row = self.con.execute(
                "select n_total,n_in,n_out,first_t,last_t from chats where chat_id=?",
                (cid,)).fetchone()
            if row:
                self.con.execute(
                    "update chats set name=?, is_group=?, n_total=?, n_in=?, n_out=?, "
                    "first_t=?, last_t=? where chat_id=?",
                    (c["name"], c["is_group"], row[0] + c["n"], row[1] + c["nin"],
                     row[2] + c["nout"],
                     min(x for x in (row[3], c["first"]) if x) if (row[3] or c["first"]) else None,
                     max(x for x in (row[4], c["last"]) if x) if (row[4] or c["last"]) else None,
                     cid))
            else:
                self.con.execute(
                    "insert into chats(chat_id,name,is_group,n_total,n_in,n_out,first_t,last_t) "
                    "values(?,?,?,?,?,?,?,?)",
                    (cid, c["name"], c["is_group"], c["n"], c["nin"], c["nout"],
                     c["first"], c["last"]))
        self.con.commit()

        import json
        self.set_meta("cursor", json.dumps(cursor))
        self.set_meta("last_import", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.set_meta("my_wxid", d.my_wxid)
        dt = time.time() - t0
        self._log(f"[+] 导入完成：扫描 {n_seen} 条，写入 {n_new} 条，耗时 {dt:.1f}s")
        return {"scanned": n_seen, "inserted": n_new, "seconds": round(dt, 1),
                "chats": len(cur_chat)}

    # ---------------- 统计 ----------------

    def stats(self) -> dict:
        c = self.con
        total = c.execute("select count(*) from messages").fetchone()[0]
        if not total:
            return {"total": 0}
        r = c.execute("select direction, count(*) from messages group by direction").fetchall()
        kinds = c.execute("select kind, count(*) from messages group by kind "
                          "order by 2 desc limit 8").fetchall()
        t0, t1 = c.execute("select min(t), max(t) from messages").fetchone()
        nchats = c.execute("select count(*) from chats").fetchone()[0]
        tok = c.execute("select sum(tok) from messages").fetchone()[0] or 0
        return {
            "total": total,
            "direction": dict(r),
            "kinds": dict(kinds),
            "chats": nchats,
            "first": time.strftime("%Y-%m-%d", time.localtime(t0)) if t0 else None,
            "last": time.strftime("%Y-%m-%d", time.localtime(t1)) if t1 else None,
            "tokens": round(tok),
            "last_import": self.get_meta("last_import"),
        }

    def top_chats(self, n=15, mine_only=False) -> list:
        sql = ("select c.chat_id, c.name, c.is_group, c.n_total, c.n_in, c.n_out, c.last_t "
               "from chats c order by c.n_total desc limit ?")
        rows = self.con.execute(sql, (n,)).fetchall()
        return [{"chat_id": r[0], "name": r[1], "is_group": bool(r[2]), "n": r[3],
                 "n_in": r[4], "n_out": r[5],
                 "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[6])) if r[6] else None}
                for r in rows]

    # ---------------- 检索 ----------------

    def search(self, q: str, chat_id: str = "", direction: str = "",
               kind: str = "", limit: int = 300, days: int = 0,
               order: str = "relevance") -> list:
        """子串检索。返回带上下文的命中列表。

        用 LIKE 而不是 FTS5：实测 FTS5 的 unicode61 对中文**不分词**
        （整句当一个 token，搜"羽毛球"匹配不到），trigram 又只支持 3 字以上。
        10 万条规模 LIKE 全表扫描只要 50~200ms，完全够用。
        """
        if not q and not chat_id:
            return []
        where, args = [], []
        if q:
            where.append("text like ?")
            args.append(f"%{q}%")
        if chat_id:
            where.append("chat_id = ?")
            args.append(chat_id)
        if direction in ("in", "out"):
            where.append("direction = ?")
            args.append(direction)
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if days:
            where.append("t >= ?")
            args.append(int(time.time()) - days * 86400)
        sql = ("select chat_id, local_id, t, direction, sender_name, kind, text, tok "
               f"from messages where {' and '.join(where)} ")
        # 排序模式：
        #   recent    —— 按时间新→旧（用户明确要"最近的"时用）
        #   short     —— 按消息长度升序（**检索默认**）：
        #                短消息更可能是关键词命中，长段落多是转发的长文/公告，
        #                按时间排会让高频词的结果被大段文本挤出榜单。
        #   relevance —— 长度升序 + 时间新
        if order == "recent":
            sql += "order by t desc "
        elif order == "short":
            sql += "order by length(text) asc "
        else:
            sql += "order by length(text) asc, t desc "
        sql += "limit ?"
        args.append(limit)
        out = []
        for r in self.con.execute(sql, args):
            out.append({"chat_id": r[0], "local_id": r[1], "t": r[2],
                        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[2])),
                        "direction": r[3], "sender": r[4], "kind": r[5],
                        "text": r[6], "tok": r[7]})
        return out

    def context_around(self, chat_id: str, local_id: int, before=6, after=6) -> list:
        """取某条消息的上下文（用于"这句话是在什么情况下说的"）。"""
        rows = self.con.execute(
            "select local_id,t,direction,sender_name,kind,text from messages "
            "where chat_id=? and local_id between ? and ? order by local_id",
            (chat_id, local_id - before, local_id + after)).fetchall()
        return [{"local_id": r[0], "time": time.strftime("%m-%d %H:%M", time.localtime(r[1])),
                 "direction": r[2], "sender": r[3], "kind": r[4], "text": r[5]}
                for r in rows]

    def chat_id_by_name(self, name: str) -> list:
        rows = self.con.execute(
            "select chat_id,name,is_group,n_total from chats where name like ? "
            "order by n_total desc limit 10", (f"%{name}%",)).fetchall()
        return [{"chat_id": r[0], "name": r[1], "is_group": bool(r[2]), "n": r[3]}
                for r in rows]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="微信消息本地库")
    ap.add_argument("cmd", choices=["import", "stats", "top", "search", "reimport"])
    ap.add_argument("q", nargs="?", default="")
    ap.add_argument("--chat", default="")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()

    s = Store()
    if a.cmd in ("import", "reimport"):
        r = s.import_all(incremental=(a.cmd == "import") and bool(s.get_meta("cursor")))
        print(r)
    st = s.stats()
    print()
    print("=" * 60)
    print(f"消息总数 {st.get('total')}  会话 {st.get('chats')}  "
          f"跨度 {st.get('first')} → {st.get('last')}")
    print(f"方向 {st.get('direction')}")
    print(f"类型 {st.get('kinds')}")
    print(f"token 合计 {st.get('tokens')}")
    print("=" * 60)
    if a.cmd == "top":
        for c in s.top_chats(a.limit):
            print(f"  {c['name'][:22]:<24} {c['n']:>7} 条  (收{c['n_in']} 发{c['n_out']})  {c['last']}")
    if a.cmd == "search" and a.q:
        for m in s.search(a.q, limit=a.limit):
            print(f"  {m['time']} [{m['sender'] or m['chat_id'][:14]}] {m['text'][:60]}")
