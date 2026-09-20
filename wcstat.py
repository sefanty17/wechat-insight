#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wcstat.py — 微信聊天字数统计（增量、多人分账）

数据链路：
  微信加密库(db_storage) --all_keys.json里的enc_key--> 解密到临时目录 --> SQLite 统计
  新消息先落在 -wal 文件里，所以解密时必须连 WAL 一起回放，否则会漏掉最近的消息。

用法：
  python wcstat.py list                 # 列出所有会话（按消息量排序），并识别哪些是你
  python wcstat.py count                # 全量统计（重新算一遍，最准）
  python wcstat.py sync                 # 增量统计：只处理上次之后的新消息（快）
  python wcstat.py watch --interval 20  # 常驻，每 20 秒同步一次（发一条就更新一次）
  python wcstat.py report               # 打印统计结果
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wccount import count_text  # noqa: E402
from wcmsg import decode  # noqa: E402

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

PAGE_SZ = 4096
RESERVE_SZ = 80
IV_SZ = 16
SALT_SZ = 16
SQLITE_HDR = b"SQLite format 3\x00"
WAL_HDR_SZ = 32
WAL_FRAME_HDR = 24

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "all_keys.json")
STATE_FILE = os.path.join(HERE, "state.json")
STATS_FILE = os.path.join(HERE, "stats.json")
TMP_DIR = os.path.join(HERE, ".tmpdec")

# 是否尝试回放 -wal。默认关：WCDB 的 WAL 格式与标准 SQLite 不同，回放有风险，
# 回放后必须通过 _wal_result_usable 验证才会采用。设环境变量 WCSTAT_WAL=1 打开。
TRY_WAL = os.environ.get("WCSTAT_WAL", "1") == "1"

# 消息类型
T_TEXT = 1          # 文本
T_IMAGE = 3         # 图片
T_VOICE = 34        # 语音
T_VIDEO = 43        # 视频
T_STICKER = 47      # 表情(GIF/贴纸)
T_SYSTEM = 10000    # 系统消息（撤回提示等）

TYPE_LABEL = {
    T_TEXT: "文本",
    T_IMAGE: "图片",
    T_VOICE: "语音",
    T_VIDEO: "视频",
    T_STICKER: "表情",
    T_SYSTEM: "系统",
}


# --------------------------------------------------------------------------
# 解密：SQLCipher4 页面解密 + WAL 回放
# --------------------------------------------------------------------------

def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """用 Windows 自带 CNG 做 AES-256-CBC 解密，免第三方库。

    注意：缓冲区一律用 create_string_buffer 传「指针」。
    不能用 c_char_p —— 它按 C 字符串处理，密钥/IV/密文里的 \\x00 会被当成结尾，
    长度参数与实际数据对不上，就会报「设置 CBC 模式失败」之类的怪错。
    """
    import ctypes
    import ctypes.wintypes as wt

    bcrypt = ctypes.WinDLL("bcrypt")
    bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
        ctypes.POINTER(wt.HANDLE), ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    bcrypt.BCryptSetProperty.argtypes = [
        wt.HANDLE, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    bcrypt.BCryptGenerateSymmetricKey.argtypes = [
        wt.HANDLE, ctypes.POINTER(wt.HANDLE), ctypes.c_void_p, ctypes.c_ulong,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    bcrypt.BCryptDecrypt.argtypes = [
        wt.HANDLE, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    for fn in ("BCryptDestroyKey", "BCryptCloseAlgorithmProvider"):
        getattr(bcrypt, fn).argtypes = [wt.HANDLE, ctypes.c_ulong]

    h_alg = wt.HANDLE()
    if bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0) != 0:
        raise RuntimeError("BCryptOpenAlgorithmProvider 失败")
    try:
        # 属性名字符串必须含结尾 NUL，长度也要算进去
        name = ctypes.create_unicode_buffer("ChainingMode")
        value = ctypes.create_unicode_buffer("ChainingModeCBC")
        if bcrypt.BCryptSetProperty(
                h_alg, name, ctypes.cast(value, ctypes.c_void_p),
                ctypes.sizeof(value), 0) != 0:
            raise RuntimeError("设置 CBC 模式失败")
        key_buf = ctypes.create_string_buffer(key, len(key))
        h_key = wt.HANDLE()
        if bcrypt.BCryptGenerateSymmetricKey(
                h_alg, ctypes.byref(h_key), None, 0,
                ctypes.cast(key_buf, ctypes.c_void_p), len(key), 0) != 0:
            raise RuntimeError("生成对称密钥失败")
        try:
            iv_buf = ctypes.create_string_buffer(iv, len(iv))
            data_buf = ctypes.create_string_buffer(data, len(data))
            out = ctypes.create_string_buffer(len(data))
            out_len = ctypes.c_ulong(0)
            if bcrypt.BCryptDecrypt(
                    h_key, ctypes.cast(data_buf, ctypes.c_void_p), len(data), None,
                    ctypes.cast(iv_buf, ctypes.c_void_p), len(iv),
                    ctypes.cast(out, ctypes.c_void_p), len(out),
                    ctypes.byref(out_len), 0) != 0:
                raise RuntimeError("BCryptDecrypt 失败")
            return out.raw[:out_len.value]
        finally:
            bcrypt.BCryptDestroyKey(h_key, 0)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def _decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    iv = page[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    if pgno == 1:
        enc = page[SALT_SZ: PAGE_SZ - RESERVE_SZ]
        dec = _aes_cbc_decrypt(enc_key, iv, enc)
        return SQLITE_HDR + dec + b"\x00" * RESERVE_SZ
    enc = page[: PAGE_SZ - RESERVE_SZ]
    dec = _aes_cbc_decrypt(enc_key, iv, enc)
    return dec + b"\x00" * RESERVE_SZ


def decrypt_db(src_db: str, enc_key: bytes, out_db: str) -> dict:
    """解密主库并回放 -wal（如果有），输出可读的标准 SQLite 文件。"""
    with open(src_db, "rb") as f:
        raw = bytearray(f.read())

    total_pages = len(raw) // PAGE_SZ
    if len(raw) % PAGE_SZ:
        total_pages += 1
        raw.extend(b"\x00" * (PAGE_SZ - (len(raw) % PAGE_SZ)))

    out = bytearray(len(raw))
    for i in range(total_pages):
        pg = bytes(raw[i * PAGE_SZ:(i + 1) * PAGE_SZ])
        out[i * PAGE_SZ:(i + 1) * PAGE_SZ] = _decrypt_page(enc_key, pg, i + 1)

    stat = {"pages": total_pages, "wal_frames": 0, "wal_applied": 0, "wal_mode": "off"}

    wal_path = src_db + "-wal"
    if TRY_WAL and os.path.exists(wal_path) and os.path.getsize(wal_path) > WAL_HDR_SZ:
        with open(wal_path, "rb") as f:
            wal = f.read()
        n_frames = (len(wal) - WAL_HDR_SZ) // (WAL_FRAME_HDR + PAGE_SZ)
        stat["wal_frames"] = n_frames

        def frame(k):
            off = WAL_HDR_SZ + k * (WAL_FRAME_HDR + PAGE_SZ)
            return (int.from_bytes(wal[off:off + 4], "big"),
                    wal[off + WAL_FRAME_HDR: off + WAL_FRAME_HDR + PAGE_SZ])

        # WCDB 的 -wal 不是标准 SQLite WAL（帧头不是明文 pgno/salt/checksum 那套），
        # 顺序无脑全量回放会覆盖出损坏的库。这里只回放「每个页号的最后一帧」，
        # 这是能同时兼顾"拿到最新页"和"不损坏"的最小假设，并且回放后要验证。
        latest = {}
        for k in range(n_frames):
            pgno, _ = frame(k)
            if pgno:
                latest[pgno] = k
        trial = bytearray(out)
        for pgno, k in sorted(latest.items(), key=lambda kv: kv[1]):
            _, enc_page = frame(k)
            if len(enc_page) < PAGE_SZ:
                continue
            if (pgno - 1) * PAGE_SZ + PAGE_SZ > len(trial):
                trial.extend(b"\x00" * ((pgno - 1) * PAGE_SZ + PAGE_SZ - len(trial)))
            trial[(pgno - 1) * PAGE_SZ:(pgno - 1) * PAGE_SZ + PAGE_SZ] = _decrypt_page(
                enc_key, enc_page, pgno)
            stat["wal_applied"] += 1

        if _wal_result_usable(trial):
            out = trial
            stat["wal_mode"] = "latest-per-page"
        else:
            stat["wal_applied"] = 0
            stat["wal_mode"] = "rejected"

    os.makedirs(os.path.dirname(out_db), exist_ok=True)
    # 原子写入：先写临时文件再替换。
    # 直接写目标文件的话，若中途被中断（或另一进程正在读），
    # 会留下一个**半截的坏库**——实测出现过 `database disk image is malformed`，
    # 而事后单独重解又是好的，就是这个原因。
    tmp_db = out_db + ".part"
    with open(tmp_db, "wb") as f:
        f.write(out)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_db, out_db)
    return stat


def _wal_result_usable(data: bytearray) -> bool:
    """验证回放 WAL 后的镜像是否还是一个能正常读的 SQLite。

    WAL 回放是「有风险的操作」：回放错了会得到损坏的库，丢数据比不回放更糟。
    所以只有验证通过才采纳，否则宁可退回「只用主库」。
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                con.execute("select count(*) from sqlite_master").fetchone()
                row = con.execute("pragma quick_check").fetchone()
                if row and row[0] != "ok":
                    return False
                return True
            finally:
                con.close()
        except sqlite3.DatabaseError:
            return False
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# 密钥 / 路径
# --------------------------------------------------------------------------

def load_keys() -> dict:
    if not os.path.exists(KEYS_FILE):
        raise SystemExit(
            f"找不到 {KEYS_FILE}\n请先跑：python tools\\wcdb_key_tool_windows.py extract")
    with open(KEYS_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"people": {}, "updated": None}


def save_stats(stats: dict) -> None:
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)


def find_db_dir() -> str:
    """从 %APPDATA%\\Tencent\\xwechat\\config\\*.ini 找数据根，再匹配 db_storage。"""
    appdata = os.environ.get("APPDATA", "")
    cfg = os.path.join(appdata, "Tencent", "xwechat", "config")
    roots = []
    if os.path.isdir(cfg):
        import glob
        for ini in glob.glob(os.path.join(cfg, "*.ini")):
            for enc in ("utf-8", "gbk"):
                try:
                    with open(ini, encoding=enc) as f:
                        content = f.read(1024).strip()
                    break
                except UnicodeDecodeError:
                    continue
            if content and os.path.isdir(content):
                roots.append(content)
    for root in roots:
        import glob
        for m in glob.glob(os.path.join(root, "xwechat_files", "*", "db_storage")):
            if os.path.isdir(m):
                return m
    raise SystemExit("未能自动定位 db_storage，请用 --db-dir 指定")


# --------------------------------------------------------------------------
# 会话与身份识别
# --------------------------------------------------------------------------

def _contact_names(db_dir: str) -> dict:
    """username -> 显示名（备注优先，其次昵称）。"""
    keys = load_keys()
    src = os.path.join(db_dir, "contact", "contact.db")
    key = keys.get("contact\\contact.db", {}).get("enc_key")
    if not key:
        return {}
    out_db = os.path.join(TMP_DIR, "contact", "contact.db")
    decrypt_db(src, bytes.fromhex(key), out_db)
    names = {}
    con = sqlite3.connect(out_db)
    try:
        for username, remark, nick in con.execute(
                "select username, remark, nick_name from contact"):
            names[username] = remark or nick or username
    finally:
        con.close()
    return names


def _group_names(db_dir: str) -> dict:
    """群聊 id -> 群名（来自 contact.db 的 chat_room 表）。"""
    keys = load_keys()
    src = os.path.join(db_dir, "contact", "contact.db")
    key = keys.get("contact\\contact.db", {}).get("enc_key")
    if not key:
        return {}
    out_db = os.path.join(TMP_DIR, "contact", "contact.db")
    if not os.path.exists(out_db):
        decrypt_db(src, bytes.fromhex(key), out_db)
    names = {}
    con = sqlite3.connect(out_db)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(chat_room)")]
        name_col = "nickname" if "nickname" in cols else ("name" if "name" in cols else None)
        user_col = "username" if "username" in cols else cols[0]
        if name_col:
            for u, n in con.execute(f"select {user_col},{name_col} from chat_room"):
                if u and n:
                    names[u] = n
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return names


def map_tables_to_sessions(con: sqlite3.Connection) -> dict:
    """表名 -> 真实会话 id。

    微信 4.x 的消息表名是 `Msg_<MD5(会话username)>`，
    所以把 Name2Id 里所有 username 取 MD5 就能反查出每张表属于哪个会话。
    这是唯一可靠的办法——表里存的 real_sender_id 名字在单聊里是 wxid、
    在群聊里却是**群成员的字段值**，光看发送者根本判断不出群聊 id。
    """
    out = {}
    try:
        users = [r[0] for r in con.execute("select user_name from Name2Id")]
    except sqlite3.Error:
        return out
    for u in users:
        if not u:
            continue
        out["Msg_" + hashlib.md5(u.encode("utf-8")).hexdigest()] = u
    return out


def session_names(db_dir: str) -> dict:
    """会话 id -> 显示名（单聊用联系人备注，群聊用群名）。"""
    names = dict(_group_names(db_dir))
    for u, n in _contact_names(db_dir).items():
        names.setdefault(u, n)
    return names


def member_names(db_dir: str) -> dict:
    """username -> 昵称，用于把群消息里的 `wxid_xxx:` 前缀换成人话。

    优先群内昵称（chatroom_member），其次联系人备注/昵称。
    微信的群消息原文形如 "wxid_abc:\\n大家好"，直接用会很难看。
    """
    names = dict(_contact_names(db_dir))
    keys = load_keys()
    src = os.path.join(db_dir, "contact", "contact.db")
    key = keys.get("contact\\contact.db", {}).get("enc_key")
    if not key:
        return names
    out_db = os.path.join(TMP_DIR, "contact", "contact.db")
    if not os.path.exists(out_db):
        try:
            decrypt_db(src, bytes.fromhex(key), out_db)
        except Exception:
            return names
    con = sqlite3.connect(out_db)
    try:
        for username, nick in con.execute(
                "select username, nick_name from contact "
                "where nick_name is not null and nick_name != ''"):
            names.setdefault(username, nick)
        # 注：chatroom_member 只有 (room_id, member_id)，不含群内昵称，
        # 所以这里拿不到"某个群里的专属昵称"，只能用全局昵称。
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return names


# 群消息原文里的发送者前缀：`wxid_xxx:\n` 或 `someid:\n`
_SENDER_PREFIX = re.compile(r"^([A-Za-z0-9_\-.]{2,64})[:：]\n")


def split_sender(text: str, is_group: bool, names: dict) -> tuple:
    """把群消息的 `发送者id:\\n正文` 拆开，id 尽量换成昵称。

    返回 (显示用发送者名, 正文)。单聊或没有前缀时发送者名为空。

    识别要点（踩过的坑）：
      · 真前缀的冒号**紧挨换行**（"wxid_abc:\\n正文"）。
        宽松成 "任意内容:" 会把 "http://x.com:8080/y"、"12:30 开会" 也拆了。
      · 候选必须像即时通讯 id：wxid_ 开头、含下划线/连字符，或能在昵称表里查到。
    """
    if not text:
        return "", text
    m = _SENDER_PREFIX.match(text)
    if not m:
        return "", text
    who = m.group(1)
    looks_like_id = (
        who.startswith("wxid_")
        or "_" in who
        or "-" in who
        or who in names
    )
    if not looks_like_id:
        return "", text
    body = text[m.end():]
    if not is_group:
        # 单聊里这个前缀是多余的，直接去掉
        return "", body
    return names.get(who, who), body


def _msg_tables(con: sqlite3.Connection) -> list:
    return [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' and name like 'Msg\\_%' escape '\\'")]


def sender_map(con: sqlite3.Connection) -> dict:
    """message 库自己的 Name2Id 才是 real_sender_id 的正确映射（务必别用 session 库的）。"""
    return {rowid: name for rowid, name in con.execute("select rowid, user_name from Name2Id")}


def identify_self(con: sqlite3.Connection, smap: dict, my_wxid: str) -> int | None:
    for rid, name in smap.items():
        if name == my_wxid:
            return rid
    return None


def resolve_my_wxid(db_dir: str) -> str:
    """数据目录名形如 wxid_xxx_7f4b，取前半段作为本人 wxid。"""
    base = os.path.basename(os.path.dirname(db_dir.rstrip("\\/")))
    if base.startswith("wxid_"):
        return base.split("_")[0] + "_" + base.split("_")[1]
    return ""


def open_message_db(db_dir: str, which: str = "message_0.db") -> str:
    """解密消息库并返回可读路径。

    **关键是验完整性再返回**：微信在写库/退出登录的瞬间，解出来可能是个
    残缺的镜像（实测会 `no such table: Name2Id`）。如果直接把这种库返回出去，
    调用方就会崩——尤其常驻 watcher 一崩就再也不记账了。
    这里的策略：解密 → 验证 Name2Id 在不在 → 不在就退避重试。
    """
    keys = load_keys()
    rel = f"message\\{which}"
    key = keys.get(rel, {}).get("enc_key")
    if not key:
        raise SystemExit(f"all_keys.json 里没有 {rel} 的密钥")
    src = os.path.join(db_dir, "message", which)
    out_db = os.path.join(TMP_DIR, "message", which)

    last_err = None
    for attempt in range(3):
        try:
            stat = decrypt_db(src, bytes.fromhex(key), out_db)
            if _valid_message_db(out_db):
                if os.environ.get("WCSTAT_DEBUG"):
                    print(f"[debug] {which}: pages={stat['pages']} "
                          f"wal_frames={stat['wal_frames']} wal_applied={stat['wal_applied']} "
                          f"wal_mode={stat['wal_mode']}")
                return out_db
            last_err = "解密结果缺少 Name2Id 表（库可能正在被写入或已损坏）"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{which} 连续 3 次都拿不到可用镜像：{last_err}")


def _valid_message_db(path: str) -> bool:
    """解密镜像是否可用。

    除了必须有 Name2Id，还要跑 quick_check —— 只验一张表是不够的：
    实测遇到过 Name2Id 读得出来、但别的表已损坏的情况，
    那样会在遍历到某张表时才炸（`database disk image is malformed`）。
    """
    if not os.path.exists(path):
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = con.execute(
            "select count(*) from sqlite_master where type='table' and name='Name2Id'"
        ).fetchone()
        if not row or not row[0]:
            return False
        con.execute("select count(*) from Name2Id").fetchone()
        # 整库完整性（quick_check 比 integrity_check 快很多，且足够发现页级损坏）
        qc = con.execute("pragma quick_check").fetchone()
        if qc and qc[0] != "ok":
            return False
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        con.close()


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------

def iter_messages(con, table, since_local_id=0, only_mine=True, my_sender_id=None):
    """产出 (local_id, sender_id, create_time, local_type, content)。"""
    sql = (f"select local_id, real_sender_id, create_time, local_type, message_content "
           f"from {table} where local_id > ? order by local_id")
    for local_id, sender, ctime, mtype, content in con.execute(sql, (since_local_id,)):
        if only_mine and my_sender_id is not None and sender != my_sender_id:
            continue
        yield local_id, sender, ctime, mtype, content


def scan(db_dir: str, only_mine: bool, which: str = "message_0.db") -> dict:
    """全量扫描 message_0.db，返回每个会话的统计。"""
    out_db = open_message_db(db_dir, which)
    con = sqlite3.connect(out_db)
    smap = sender_map(con)
    my_wxid = resolve_my_wxid(db_dir)
    me = identify_self(con, smap, my_wxid)
    names = _contact_names(db_dir)
    tables = _msg_tables(con)

    result = {}
    for table in tables:
        total = con.execute(f"select count(*) from {table}").fetchone()[0]
        if total == 0:
            continue
        agg = {
            "table": table, "msgs": 0, "cjk": 0, "word": 0, "digit": 0,
            "emoji": 0, "punct": 0, "chars": 0,
            "first_time": None, "last_time": None, "last_local_id": 0,
            "send_counts": {}, "text_len_sum": 0, "max_len": 0,
        }
        other_ids = set()
        other_count = {}
        sql = ("select local_id, real_sender_id, create_time, local_type, message_content "
               f"from {table} order by local_id")
        for local_id, sender, ctime, mtype, content in con.execute(sql):
            agg["last_local_id"] = max(agg["last_local_id"], local_id)
            if sender != me:
                other_ids.add(sender)
                other_count[sender] = other_count.get(sender, 0) + 1
                continue
            if mtype != T_TEXT or not content:
                continue
            c = count_text(content)
            agg["msgs"] += 1
            agg["cjk"] += c["cjk"]
            agg["word"] += c["word"]
            agg["digit"] += c["digit"]
            agg["emoji"] += c["emoji"]
            agg["punct"] += c["punct"]
            agg["chars"] += c["chars"]
            agg["text_len_sum"] += c["total"]
            agg["max_len"] = max(agg["max_len"], c["total"])
            sk = str(sender)
            agg["send_counts"][sk] = agg["send_counts"].get(sk, 0) + 1
            if agg["first_time"] is None:
                agg["first_time"] = ctime
            agg["last_time"] = ctime

        if agg["msgs"] == 0 and not only_mine:
            pass
        # 会话对象：取「有有效 wxid 且消息最多」的那个对方。
        # 注意必须过滤掉 wxid 为空的 sender（微信的系统提示/占位记录，
        # real_sender_id 会指向 Name2Id 里 user_name='' 的那一行）。
        valid_others = [s for s in other_ids if smap.get(s)]
        peer_id = None
        if valid_others:
            peer_id = max(valid_others, key=lambda s: other_count.get(s, 0))
        peer_wxid = smap.get(peer_id) if peer_id is not None else None
        agg["peer_id"] = peer_id
        agg["peer_wxid"] = peer_wxid
        agg["peer_name"] = names.get(peer_wxid, peer_wxid) if peer_wxid else "(未识别)"
        # 群聊：会话 id 以 @chatroom 结尾，或存在多个有 wxid 的发送者
        agg["is_group"] = bool(peer_wxid) and (
            str(peer_wxid).endswith("@chatroom") or len(valid_others) > 1)
        agg["total_rows"] = total
        result[table] = agg
    con.close()
    return {"self": {"wxid": my_wxid, "sender_id": me, "name": names.get(my_wxid, my_wxid)},
            "db_dir": db_dir, "chats": result}


def cmd_list(args) -> None:
    db_dir = args.db_dir or find_db_dir()
    data = scan(db_dir, only_mine=True, which=args.db)
    print(f"本人: {data['self']['name']} ({data['self']['wxid']})  "
          f"real_sender_id={data['self']['sender_id']}")
    print(f"数据: {data['db_dir']}\n")
    rows = sorted(data["chats"].values(), key=lambda a: -a["text_len_sum"])
    print(f"{'序号':<4}{'条目':<28}{'条数':>7}{'总字数':>9}{'汉字':>9}{'英文词':>7}{'表情':>6}")
    print("-" * 76)
    for i, a in enumerate(rows, 1):
        if a["msgs"] == 0:
            continue
        print(f"{i:<4}{a['peer_name'][:26]:<28}{a['msgs']:>7}{a['text_len_sum']:>9}"
              f"{a['cjk']:>9}{a['word']:>7}{a['emoji']:>6}")
    print(f"\n共 {len([a for a in rows if a['msgs'] > 0])} 个与我发过文本的会话")


def cmd_count(args) -> None:
    """全量重算。注意必须同时把 last_local_id 基线写进 state.json，
    否则随后的 sync 会从 0 重新数一遍，与这里的结果重复累加。"""
    db_dir = args.db_dir or find_db_dir()
    data = scan(db_dir, only_mine=True, which=args.db)
    stats = {"self": data["self"], "people": {}, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    state = load_state()
    for a in data["chats"].values():
        # 无论本人有没有发过文本，都要推进基线
        state.setdefault(a["table"], {})["last_local_id"] = a["last_local_id"]
        if a["msgs"] == 0:
            continue
        key = a["peer_wxid"] or a["table"]
        stats["people"][key] = {
            "name": a["peer_name"], "msgs": a["msgs"], "cjk": a["cjk"],
            "word": a["word"], "digit": a["digit"], "emoji": a["emoji"],
            "punct": a["punct"], "chars": a["chars"], "total": a["text_len_sum"],
            "max_len": a["max_len"], "first_time": a["first_time"],
            "last_time": a["last_time"], "last_local_id": a["last_local_id"],
            "is_group": a["is_group"],
        }
    state["_fingerprint"] = _db_fingerprint(db_dir, args.db)
    save_state(state)
    save_stats(stats)
    print(f"[+] 全量统计完成，{len(stats['people'])} 个会话 -> {STATS_FILE}")


def _db_fingerprint(db_dir: str, which: str) -> dict:
    """主库 + -wal 的大小与修改时间。变了才需要解密，否则整轮同步可以秒回。"""
    mp = os.path.join(db_dir, "message", which)
    fp = {}
    for suffix in ("", "-wal"):
        p = mp + suffix
        try:
            st = os.stat(p)
            fp[suffix or "db"] = [st.st_size, int(st.st_mtime)]
        except OSError:
            fp[suffix or "db"] = None
    return fp


def cmd_sync(args) -> list:
    """增量：只处理 local_id > 上次记录的会话，返回新增明细 [(名字, 字数), ...]。"""
    db_dir = args.db_dir or find_db_dir()
    state = load_state()
    stats = load_stats()

    # 快速路径：库文件没动过就直接返回，不做 130MB 解密
    fp = _db_fingerprint(db_dir, args.db)
    if state.get("_fingerprint") == fp:
        return []

    out_db = open_message_db(db_dir, args.db)
    con = sqlite3.connect(out_db)
    smap = sender_map(con)
    my_wxid = resolve_my_wxid(db_dir)
    me = identify_self(con, smap, my_wxid)
    if me is None:
        print("[!] 未能识别本人 sender_id，跳过")
        return []
    names = _contact_names(db_dir)
    stats.setdefault("people", {})
    events = []

    for table in _msg_tables(con):
        # 会话对象：取该表里第一个非本人 sender
        row = con.execute(
            f"select real_sender_id from {table} where real_sender_id != ? limit 1", (me,)).fetchone()
        if not row:
            continue
        peer_wxid = smap.get(row[0])
        if not peer_wxid:
            continue
        since = state.get(table, {}).get("last_local_id", 0)
        # 防线：如果这张表的最大 local_id 已经 <= 上次记录，说明已经数过，跳过
        max_id = con.execute(f"select max(local_id) from {table}").fetchone()[0] or 0
        if max_id <= since:
            continue
        added = 0
        for local_id, sender, ctime, mtype, content in con.execute(
                f"select local_id, real_sender_id, create_time, local_type, message_content "
                f"from {table} where local_id > ? order by local_id", (since,)):
            state.setdefault(table, {})["last_local_id"] = local_id
            if sender != me or mtype != T_TEXT or not content:
                continue
            c = count_text(content)
            p = stats["people"].setdefault(peer_wxid, {
                "name": names.get(peer_wxid, peer_wxid), "msgs": 0, "cjk": 0, "word": 0,
                "digit": 0, "emoji": 0, "punct": 0, "chars": 0, "total": 0,
                "max_len": 0, "first_time": None, "last_time": None,
                "last_local_id": 0, "is_group": str(peer_wxid).endswith("@chatroom"),
            })
            p["msgs"] += 1
            for k in ("cjk", "word", "digit", "emoji", "punct", "chars"):
                p[k] += c[k]
            p["total"] += c["total"]
            p["max_len"] = max(p["max_len"], c["total"])
            p["last_local_id"] = max(p["last_local_id"], local_id)
            if p["first_time"] is None:
                p["first_time"] = ctime
            p["last_time"] = ctime
            added += 1
            events.append((p["name"], c["total"]))
            if args.verbose and args.interval == 0:
                print(f"  +{c['total']}字 [{p['name']}] {content[:40]}")
    con.close()
    state["_fingerprint"] = fp
    save_state(state)
    stats["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_stats(stats)
    return events


def cmd_sync_cli(args) -> int:
    events = cmd_sync(args)
    if not events:
        print("[+] 无新增")
    else:
        print(f"[+] 新增 {len(events)} 条、{sum(e[1] for e in events)} 字")
        per = {}
        for name, n in events:
            per[name] = per.get(name, 0) + n
        for name, n in sorted(per.items(), key=lambda kv: -kv[1])[:10]:
            print(f"      {name}: {n} 字")
    return 0


def cmd_watch(args) -> None:
    print(f"[*] 常驻监听，每 {args.interval} 秒同步一次（Ctrl+C 退出）")
    # 先做一次全量，确保历史数据完整、state 对齐
    db_dir = args.db_dir or find_db_dir()
    data = scan(db_dir, only_mine=True, which=args.db)
    state = load_state()
    for table, a in data["chats"].items():
        state.setdefault(table, {})["last_local_id"] = a["last_local_id"]
    state["_fingerprint"] = _db_fingerprint(db_dir, args.db)
    save_state(state)
    stats = {"self": data["self"], "people": {}, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    for a in data["chats"].values():
        if a["msgs"] == 0:
            continue
        stats["people"][a["peer_wxid"] or a["table"]] = {
            "name": a["peer_name"], "msgs": a["msgs"], "cjk": a["cjk"], "word": a["word"],
            "digit": a["digit"], "emoji": a["emoji"], "punct": a["punct"],
            "chars": a["chars"], "total": a["text_len_sum"], "max_len": a["max_len"],
            "first_time": a["first_time"], "last_time": a["last_time"],
            "last_local_id": a["last_local_id"], "is_group": a["is_group"],
        }
    save_stats(stats)
    print(f"[*] 基线完成：{len(stats['people'])} 个会话")

    args.interval = int(args.interval)
    args.verbose = True
    fails = 0
    try:
        while True:
            time.sleep(args.interval)
            try:
                events = cmd_sync(args)
                fails = 0
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # 常驻进程绝不能因为一次瞬时故障（微信退出、库正在写、密钥过期）就死掉，
                # 否则"发一条更新一次"就永久失效了。
                fails += 1
                wait = min(60, args.interval * fails)
                print(f"[!] 第 {fails} 次同步失败：{type(e).__name__}: {e}"
                      f"；{wait}s 后重试")
                time.sleep(wait)
                continue
            if events:
                ts = time.strftime("%H:%M:%S")
                per = {}
                for name, n in events:
                    per[name] = per.get(name, 0) + n
                detail = "，".join(f"{k} +{v}字" for k, v in
                                   sorted(per.items(), key=lambda kv: -kv[1])[:3])
                print(f"[{ts}] 新增 {len(events)} 条（{detail}）")
    except KeyboardInterrupt:
        print("\n[*] 已停止")


def iter_new_messages(db_dir: str, which: str = "message_0.db", since_map: dict | None = None,
                      since_time: int = 0):
    """增量取「解码好的消息」。

    给 Web 界面复用：一次解密，产出所有会话里 local_id > since 的消息。
    since_map: {table: last_local_id}，缺省视为 0（全量）。
    since_time: unix 秒，只取 create_time >= 这个时间的消息（用于"只回填最近 N 天"，
                避免首次启动把几年的历史全灌进内存）。

    产出 (table, sess, direction, local_id, create_time, local_type, decoded, is_group)
    direction: "in" = 对方发来， "out" = 我发的， "sys" = 系统消息
    """
    since_map = since_map or {}
    out_db = open_message_db(db_dir, which)
    con = sqlite3.connect(out_db)
    try:
        smap = sender_map(con)
        my_wxid = resolve_my_wxid(db_dir)
        me = identify_self(con, smap, my_wxid)
        if me is None:
            return
        table2sess = map_tables_to_sessions(con)
        for table in _msg_tables(con):
            # 会话 id 优先用「表名 = MD5(会话id)」反查，这是唯一可靠的办法。
            # 表里的 real_sender_id 在单聊里映射到 wxid、在群聊里映射到群成员，
            # 光看发送者会把群聊误命名成某个成员（实测踩过）。
            sess = table2sess.get(table)
            if not sess:
                # 兜底：单聊取唯一的那个有效发送者
                valid = {s for s, _ in con.execute(
                    f"select real_sender_id, count(*) from {table} "
                    f"where real_sender_id != ? group by real_sender_id", (me,))
                    if smap.get(s)}
                if not valid:
                    continue
                sess = smap[max(valid)]
            is_group = str(sess).endswith("@chatroom")
            since = int(since_map.get(table, 0))
            sql = (f"select local_id, real_sender_id, create_time, local_type, message_content "
                   f"from {table} where local_id > ?")
            args = [since]
            if since_time:
                sql += " and create_time >= ?"
                args.append(int(since_time))
            sql += " order by local_id"
            for local_id, sender, ctime, mtype, content in con.execute(sql, args):
                if sender == me:
                    direction = "out"
                else:
                    # 注意：解析不出来也要按「对方发来」算，不能当成系统消息。
                    # 群聊里 real_sender_id 常指向不在本表 Name2Id 里的群成员
                    # （典型是「拍了拍」这类互动），它们确实是真人发出的内容。
                    # 只有真的系统提示（local_type=10000）才该被排除，
                    # 那个由 decode() 的 kind=='system' 负责判断。
                    direction = "in"
                yield (table, sess, direction, local_id, ctime, mtype,
                       decode(mtype, content), is_group)
    finally:
        con.close()


def cmd_report(_args) -> None:
    stats = load_stats()
    if not stats.get("people"):
        print("还没有数据，先跑 count 或 watch")
        return
    self_info = stats.get("self", {})
    print(f"本人: {self_info.get('name')} ({self_info.get('wxid')})")
    print(f"更新时间: {stats.get('updated')}\n")
    rows = sorted(stats["people"].values(), key=lambda p: -p["total"])
    print(f"{'对象':<24}{'条数':>7}{'总字数':>9}{'汉字':>9}{'英文词':>7}{'数字':>6}{'表情':>6}{'标点':>6}{'最长':>6}")
    print("-" * 90)
    for p in rows:
        print(f"{p['name'][:22]:<24}{p['msgs']:>7}{p['total']:>9}{p['cjk']:>9}"
              f"{p['word']:>7}{p['digit']:>6}{p['emoji']:>6}{p['punct']:>6}{p['max_len']:>6}")
    tot_msgs = sum(p["msgs"] for p in rows)
    tot = sum(p["total"] for p in rows)
    print("-" * 90)
    print(f"{'合计':<24}{tot_msgs:>7}{tot:>9}{sum(p['cjk'] for p in rows):>9}"
          f"{sum(p['word'] for p in rows):>7}{sum(p['digit'] for p in rows):>6}"
          f"{sum(p['emoji'] for p in rows):>6}{sum(p['punct'] for p in rows):>6}")


def main() -> None:
    ap = argparse.ArgumentParser(description="微信聊天字数统计")
    ap.add_argument("--db-dir", default=None, help="db_storage 路径")
    ap.add_argument("--db", default="message_0.db", help="消息库文件名")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出会话并识别本人")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("count", help="全量统计")
    p.set_defaults(func=cmd_count)

    p = sub.add_parser("sync", help="增量统计一次")
    p.add_argument("--interval", type=int, default=0)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=lambda a: cmd_sync_cli(a))

    p = sub.add_parser("watch", help="常驻增量统计")
    p.add_argument("--interval", type=int, default=20)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("report", help="打印统计结果")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    rc = args.func(args)
    if isinstance(rc, int) and rc:
        sys.exit(rc)


if __name__ == "__main__":
    os.makedirs(TMP_DIR, exist_ok=True)
    main()
