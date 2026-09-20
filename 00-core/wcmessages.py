# -*- coding: utf-8 -*-
"""
wcmessages.py — 消息读取与**发送者身份判定**（整个项目最核心的一块）

═══════════════════════════════════════════════════════════════════
  发送者判定：微信 4.x 的一个大坑
═══════════════════════════════════════════════════════════════════
  `real_sender_id` 的含义**在单聊和群聊里完全不同**：
    · 单聊：是 `Name2Id` 表的 rowid（实测本人 = 2）
    · 群聊：是**群成员序号**，和 Name2Id 毫无关系（一个群有 160 个发送者）

  所以群聊里"我的消息数"用 real_sender_id 判断全是错的
  （实测某些群算出 0 条、某些算出 137 条，都不对）。

  **可靠判据（已用 4 个群交叉验证，100% 成立）：**
    1. 群消息正文若带 `wxid_xxx:\n` 前缀 → 那是**别人**发的
       （微信只给别人的消息加前缀，从不为自己的消息加）
    2. 不带前缀 + `real_sender_id == 本人的 Name2Id rowid` → 是**我**发的

  验证数据：
    群 52201830860  有前缀 2900 条中是我的 = 0；无前缀 80 条中是我的 = 80
    群 49244530653  有前缀   58 条中是我的 = 0；无前缀 28 条中是我的 = 28
    群 45645513094  有前缀   37 条中是我的 = 0；无前缀 13 条中是我的 = 13

  单聊里也可能出现前缀（少见），同样按"有前缀=别人"处理。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 群消息发送者前缀：`wxid_xxx:\n` 或 `someid:\n`
SENDER_PREFIX = re.compile(r"^([A-Za-z0-9_\-.]{2,64})[:：]\n")


class WeChatData:
    """微信本地数据的只读访问层。"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._con = None
        self._db_dir = None
        self._my_wxid = None
        self._my_rowid = None
        self._smap = {}
        self._names = None
        self._t2s = None

    # ---------------- 底层 ----------------

    def _log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    @property
    def db_dir(self) -> str:
        if self._db_dir is None:
            import wcstat
            self._db_dir = wcstat.find_db_dir()
        return self._db_dir

    def connect(self) -> sqlite3.Connection:
        """解密（如果还没解）并连接消息库。"""
        if self._con is not None:
            return self._con
        import wcstat
        self._log("[*] 解密消息库…")
        t0 = time.time()
        path = wcstat.open_message_db(self.db_dir)
        self._con = sqlite3.connect(path)
        self._log(f"    完成（{time.time()-t0:.1f}s）")
        return self._con

    @property
    def my_wxid(self) -> str:
        if self._my_wxid is None:
            import wcstat
            self._my_wxid = wcstat.resolve_my_wxid(self.db_dir)
        return self._my_wxid

    @property
    def my_rowid(self) -> int:
        """本人的 Name2Id rowid（仅在单聊里作为 real_sender_id 有意义）。"""
        if self._my_rowid is None:
            import wcstat
            con = self.connect()
            self._my_rowid = wcstat.identify_self(con, self.sender_map, self.my_wxid)
        return self._my_rowid

    @property
    def sender_map(self) -> dict:
        if not self._smap:
            import wcstat
            self._smap = wcstat.sender_map(self.connect())
        return self._smap

    @property
    def table2session(self) -> dict:
        if self._t2s is None:
            import wcstat
            self._t2s = wcstat.map_tables_to_sessions(self.connect())
        return self._t2s

    @property
    def names(self) -> dict:
        """会话 id -> 显示名（群名/备注）。"""
        if self._names is None:
            import wcstat
            try:
                self._names = wcstat.session_names(self.db_dir)
            except Exception:
                self._names = {}
        return self._names

    @property
    def member_names(self) -> dict:
        """wxid -> 昵称（用于把群消息前缀里的 wxid 换成人话）。"""
        import wcstat
        try:
            return wcstat.member_names(self.db_dir)
        except Exception:
            return {}

    def msg_tables(self):
        import wcstat
        return wcstat._msg_tables(self.connect())

    # ---------------- 核心：遍历消息 ----------------

    def iter_messages(self, tables=None, since_local_id: dict | None = None,
                      batch_log: bool = False):
        """遍历所有消息，**正确判定发送者**。

        产出 dict:
          table, sess, chat_name, is_group, local_id, t(秒),
          direction('in'|'out'), sender_wxid, sender_name,
          kind, text, local_type, is_mine
        """
        con = self.connect()
        import wcstat
        since = since_local_id or {}
        me_rowid = self.my_rowid
        my = self.my_wxid
        mnames = None                 # 延迟加载，只有群消息才需要
        tables = tables or self.msg_tables()
        n = 0
        t0 = time.time()

        for table in tables:
            sess = self.table2session.get(table) or ""
            is_group = str(sess).endswith("@chatroom")
            chat_name = self.names.get(sess) or sess
            start = int(since.get(table, 0))
            sql = (f"select local_id, real_sender_id, create_time, local_type, message_content "
                   f"from {table} where local_id > ? order by local_id")
            try:
                cur = con.execute(sql, (start,))
            except sqlite3.Error:
                continue

            for local_id, sender_id, ctime, ltype, content in cur:
                dec = wcstat.decode(ltype, content)
                raw = dec.get("text") or ""
                kind = dec.get("kind") or "text"

                # ---- 发送者判定 ----
                # 前缀的有效性判定需要昵称表（有些发送者 id 是纯字母数字，
                # 比如 xiaomin8210、q196333966，光看格式分不出来），
                # 所以这里先把表准备好，只加载一次。
                if mnames is None:
                    mnames = self.member_names

                sender_wxid = None
                text = raw
                m = SENDER_PREFIX.match(raw)
                if m and _looks_like_id(m.group(1), mnames, my):
                    # 带前缀 = 别人发的（微信从不为自己的消息加前缀）
                    sender_wxid = m.group(1)
                    text = raw[m.end():]
                    direction = "in"
                elif sender_id == me_rowid:
                    # 不带前缀 + 实为本人 = 我发的
                    sender_wxid = my
                    direction = "out"
                elif is_group:
                    # 群聊里不带前缀又不是我 → 无法确定具体是谁，但肯定不是我
                    direction = "in"
                else:
                    # 单聊里不是我 = 对方
                    sender_wxid = self.sender_map.get(sender_id)
                    direction = "in"

                # 发送者显示名
                if direction == "out":
                    sname = "我"
                elif sender_wxid:
                    sname = mnames.get(sender_wxid) or sender_wxid
                else:
                    sname = chat_name

                n += 1
                if batch_log and n % 20000 == 0:
                    self._log(f"    …已读 {n} 条（{time.time()-t0:.0f}s）")

                yield {
                    "table": table, "sess": sess, "chat_name": chat_name,
                    "is_group": is_group, "local_id": local_id, "t": int(ctime or 0),
                    "direction": direction, "sender_wxid": sender_wxid,
                    "sender_name": sname, "kind": kind, "text": text,
                    "local_type": ltype, "is_mine": direction == "out",
                    "countable": bool(dec.get("countable")),
                }


def _looks_like_id(s: str, names=None, my_wxid: str = "") -> bool:
    """判断前缀里的串是不是一个发送者 id。

    不能只看格式——很多发送者 id 是纯字母数字（xiaomin8210、q196333966），
    格式上和"12:30"这种时间没区别。所以：
      · wxid_ 开头 / 含下划线 / 含连字符 → 是 id
      · 就是我自己 → 是 id（防串味）
      · 能在成员昵称表里查到 → 是 id
      · 纯字母数字且长度 >= 8 → 大概率是微信号/id（时间是 4~5 位）
      · 其余（短纯数字等）→ 不是前缀，按正文处理
    """
    if not s:
        return False
    if s.startswith("wxid_") or "_" in s or "-" in s:
        return True
    if my_wxid and s == my_wxid:
        return True
    if names and s in names:
        return True
    if re.fullmatch(r"[A-Za-z0-9]{8,64}", s):
        return True
    return False


# ---------------- 自测 ----------------

def _selftest():
    d = WeChatData()
    print(f"本人 wxid: {d.my_wxid}   Name2Id rowid: {d.my_rowid}")
    print(f"会话数: {len(d.table2session)}")
    print()
    print("抽查：只列**我确实发过言**的群（否则全是 0，看不出对错）")
    print(f"{'群':<26}{'总数':>7}{'我发的':>8}{'别人':>8}  我发的样本")
    print("-" * 92)
    checked = 0
    mine_total = 0
    for table in d.msg_tables():
        sess = d.table2session.get(table) or ""
        if not str(sess).endswith("@chatroom"):
            continue
        mine, other, samples = 0, 0, []
        for m in d.iter_messages(tables=[table]):
            if m["is_mine"]:
                mine += 1
                if len(samples) < 2 and m["kind"] == "text" and len(m["text"]) > 1:
                    samples.append(m["text"][:24])
            else:
                other += 1
        mine_total += mine
        if mine == 0:
            continue
        name = d.names.get(sess) or sess
        print(f"{name[:24]:<26}{mine+other:>7}{mine:>8}{other:>8}  {samples}")
        checked += 1
        if checked >= 8:
            break
    print(f"\n注：全部会话里我发出的消息总量会在大统计里体现，这里只看抽样。")
    print()
    print("抽查：单聊里的「我发的 / 对方」")
    print(f"{'会话':<26}{'总数':>7}{'我发的':>8}{'对方':>8}")
    print("-" * 92)
    c2 = 0
    for table in d.msg_tables():
        sess = d.table2session.get(table) or ""
        if str(sess).endswith("@chatroom"):
            continue
        mine = other = 0
        for m in d.iter_messages(tables=[table]):
            mine += 1 if m["is_mine"] else 0
            other += 0 if m["is_mine"] else 1
        if mine + other < 200:
            continue
        print(f"{(d.names.get(sess) or sess)[:24]:<26}{mine+other:>7}{mine:>8}{other:>8}")
        c2 += 1
        if c2 >= 6:
            break


if __name__ == "__main__":
    _selftest()
