# -*- coding: utf-8 -*-
"""
wcagenda.py — 约定挖掘机（02-report 的一部分）

从聊天记录里挖出：
  · **我答应对方的事**（承诺）
  · **对方要求我做的事**（待办）
  · **约好的时间/地点**（约定）
  · **我要求对方做的事**（我发出的请求）

并追踪兑现状态 —— 因为最有用的一句话是：
  "你三周前答应给他发报告，到现在还没提过这事"

实现要点：
  · 分块喂给 LLM（每块 ~40 条连续消息），保留对话上下文才判得准
  · 用 deepseek-chat（无 reasoning，1s 出 JSON）
  · 结果落 SQLite，**按 (chat_id, 事项指纹) 去重**，重复扫描不会重复入库
  · 抽取与"是否兑现"分开：先抽出来，兑现与否靠后续消息匹配判断
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "00-core")
for p in (CORE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from wcstore import Store  # noqa: E402
from wcllm import LLM, MODEL_FAST  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, "00-core", "chat.db")

SCHEMA = """
create table if not exists agenda (
  id        integer primary key autoincrement,
  fp        text unique,          -- 指纹：去重用
  chat_id   text,
  chat_name text,
  local_id  integer,              -- 依据消息
  t         integer,              -- 该消息时间
  day       text,
  kind      text,                 -- promise(我承诺) / task(对方要求我) /
                                  -- plan(约定) / request(我请求对方)
  who       text,                 -- 我 / 对方 / 共同
  what      text,                 -- 事项
  when_text text,                 -- 时间描述（原文）
  when_t    integer,              -- 解析出的时间戳（能解析才填）
  quote     text,                 -- 原文片段（证据）
  status    text default 'open',  -- open / done / expired / dropped
  checked   integer default 0,    -- 是否已做过兑现核查
  created   text
);
create index if not exists idx_ag_t    on agenda(t);
create index if not exists idx_ag_chat on agenda(chat_id);
create index if not exists idx_ag_kind on agenda(kind);
create index if not exists idx_ag_stat on agenda(status);
"""

EXTRACT_SYS = """你从微信聊天片段里抽取「**发生在对话双方之间**的约定与承诺」。
只输出 JSON 数组。

判断标准（宁缺勿滥，没有就返回 []）：
· promise —— **我或对方明确答应了、且与另一方有关**的事
· task    —— 一方要求另一方做某件事，而被要求方没明确答应
· plan    —— 双方约定了一个**具体的时间或地点**事项
· request —— 我要求对方做某件事

⚠️ **必须排除**（这些是最容易误抽的，请严格过滤）：
1. **转述/陈述各自的日程**——"我明天要练车""我下周考试""我后天去补牙"，
   这只是说自己的安排，**不是和对方的约定**。
2. **对第三方的承诺**——"我答应了别人""我妈让我问你"，
   这是跟**别人**的事，不属于当前对话双方。
3. **假设/设想/回忆**——"如果…的话""本来想…""之前说过"。
4. 纯闲聊、表情、广告、通知、群公告、寒暄（"在吗""吃了吗"）。
5. **已完成的**——"发你了""我弄好了"是完成，不是承诺。
6. 泛指而没具体事项的——"有空聊""改天说"。

⚠️ **关于引用**：带 `[引用]` 前缀的消息是**引用了别人的话**，
   不能把引用的内容当成说话人自己的承诺。

⚠️ **「谁负义务」必须判断准**（这是第二容易错的地方）：
   · "你能不能带我打球" → 这是**请求对方**（request / who=对方）
     不是承诺！商量语气（能不能 / 要不 / 可以吗 / 行不行）都是在**请求**
   · "我之后可能需要你来补" → 也是**请求对方**，不是自己承诺
   · "过两天我请你看电影" → 这才是**自己承诺**（promise / who=我）
   · "你给洋洋捋一下" → **要求对方做**（their_task 方向）
   判断口诀：**谁要做这件事，谁就负义务**。看动作的执行者是谁。

⚠️ **必须排除**（第三容易错）：
   · **叙述自己要做什么**——"去医院""我明天回家"，这是日程，不是请求
   · **抱怨/表达情绪**——"不要这样跟我说话""你别烦我"，这是情绪，
     不是可执行的请求（可执行请求要具体到"做什么"）
   · **假设/设想/回忆**——"如果…的话""本来想…""之前说过"
   · 纯闲聊、表情、广告、通知、群公告、寒暄（"在吗""吃了吗"）
   · **已完成的**——"发你了""我弄好了"是完成，不是承诺
   · 泛指而没具体事项的——"有空聊""改天说"

每条格式：
{"type":"promise|task|plan|request",
 "who":"我|对方|共同",          ← **谁负这个义务**（谁去做）；"共同"只在双方一起做时用
 "what":"事项，动词开头，20字内，不要带时间",
 "when":"时间描述，用原文说法；没有则空",
 "quote":"原文片段，20字内",
 "msg_index":"依据本块第几条消息，从0开始"}

只输出 JSON 数组，不要解释。"""


def _maybe_third_party(quote: str) -> bool:
    """粗略判断这条是不是在说「跟第三方的事」（不该计入当前会话）。

    实测误抽案例："我答应了别人的，所以加你回来已经是有点过界了"
    —— 那是对第三方的承诺，不属于当前对话双方。
    """
    if not quote:
        return False
    pats = ["答应别人", "答应了别人", "跟别人", "和别人约", "答应她", "答应他",
            "我妈让我", "我爸让我", "老师说", "公司让", "答应了其他"]
    return any(p in quote for p in pats)


def _is_complaint(text: str) -> bool:
    """判断这是不是「抱怨/情绪表达」而不是可执行的请求或承诺。

    实测误抽（2026-04-24 jang-jawan.）：
      "不要保存这个视频" / "不要这样跟我说话"
    —— 这是情绪和要求对方改变态度，不是一件"可执行、可勾掉"的事。
    这类进待办清单会显得很怪（你没法"完成"它）。
    """
    if not text:
        return False
    pats = ["不要这样", "别这样", "不要跟我", "别跟我", "不要再说", "别再说",
            "不要烦", "别烦", "你能不能别", "少来", "别闹", "烦不烦"]
    return any(p in text for p in pats)


def _looks_like_own_schedule(text: str) -> bool:
    """判断这句话是不是「在陈述自己的日程」，而不是和对方的约定。

    实测误抽案例（都在对方的话里）：
      "我明天要练车了" / "我下周考试" / "我后天去补牙" / "一想到我下周要去考科目二"
    这些只是说自己要干什么，和对方没关系，不该算约定。

    判据（放宽后）：
      · 出现"我" + 时间词（或紧邻个人事务词）
      · 且**没有第二人称参与**（没有"你""咱""一起""帮你"）
    注意不要把动词写死：中文里"我下周考试""我明天课"这种名词作谓语很常见，
    强行要求"要去/得去"会漏掉一大半。
    """
    if not text:
        return False
    if any(k in text for k in ("你", "咱", "一起", "帮你", "陪我", "给我", "帮你带")):
        return False          # 提到对方 → 可能是约定，不排除
    personal = ("考试", "科目", "练车", "上课", "补牙", "上班", "开会", "写作业",
                "复习", "睡觉", "吃饭", "回家", "洗澡", "拍照", "剪", "做视频",
                "课", "面试", "体测", "实验", "答辩", "实习", "练", "训练")
    has_personal = any(k in text for k in personal)
    if not has_personal:
        return False
    # "我" 出现在句子里（允许前面有"一想到""然后"等），且带时间词
    if not re.search(r"我", text):
        return False
    if re.search(r"(今天|明天|后天|大后天|下周|下星期|下个月|这周|周末|待会|等会|早上|晚上|下午)", text):
        return True
    return False


# ⚠️ 已知误判（这个过滤器是**粗筛**，宁可放过也不误杀）：
#   · "我明天回家可能" 会被判成"陈述日程"排除掉，
#     但它其实可能是对对方的暗示（"可能回去，要不要见"）。
#     这类模糊表达靠规则判不准，所以设计上让 LLM 提示词先管，
#     过滤器只处理**明显**的转述（"我今天要练车"这种）。
#     所以规则偏保守：只在同时有"我 + 时间词 + 个人事务词"时才排除。


def _fp(chat_id: str, what: str) -> str:
    """事项指纹：同一件事在不同时间被抽到要能去重。"""
    s = re.sub(r"[\s，。、！？,.!?~～]", "", (what or ""))[:24]
    return hashlib.md5(f"{chat_id}|{s}".encode()).hexdigest()[:16]


# 时间描述 → 时间戳（能解析的才转，解析不了就留空）
_REL_DAYS = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3, "昨天": -1, "前天": -2}
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_DATE_RE = re.compile(r"(\d{1,2})\s*[月/]\s*(\d{1,2})")


def parse_when(text: str, base_ts: int) -> int:
    """把「明天」「周五之前」「3月5日」这类描述转成大致时间戳。解析不了返回 0。

    实现要点（踩过的坑）：
      基准时间是**中午**（消息发在上午还是下午不该影响"哪天"的判断），
      所以先把基准归一到**当天 00:00** 再算天差。
      否则会出现：周六中午问"周末"→ (5-5)%7=0 → 算成周六（就是当天），
      而周末显然应该指即将到来的**周日**。
    """
    if not text or not base_ts:
        return 0
    t = text.strip()
    lt = time.localtime(base_ts)
    # 归一化到当天 00:00，避免"中午基准"导致的天数偏差
    midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                0, 0, 0, 0, 0, -1)))
    wday = lt.tm_wday          # 0=周一 … 6=周日

    for k, d in _REL_DAYS.items():
        if k in t:
            return midnight + d * 86400

    m = re.search(r"(?:下周|下星期)([一二三四五六日天])", t)
    if m:
        return midnight + (7 + _WEEKDAY[m.group(1)] - wday) * 86400

    m = re.search(r"(?:这|本)?(?:周|星期)([一二三四五六日天])", t)
    if m:
        delta = _WEEKDAY[m.group(1)] - wday
        if delta < 0:
            delta += 7        # 这周已过的那天 → 顺延到下周
        return midnight + delta * 86400

    if "周末" in t:
        # 周末 = 即将到来的周六。若今天就是周六，则指明天（周日）
        delta = (5 - wday) % 7
        if delta == 0:
            delta = 1
        return midnight + delta * 86400

    m = _DATE_RE.search(t)
    if m:
        mo, day = int(m.group(1)), int(m.group(2))
        try:
            ts = int(time.mktime((lt.tm_year, mo, day, 0, 0, 0, 0, 0, -1)))
            # 光有月日且已过去 → 可能是明年的
            if ts < midnight - 180 * 86400:
                ts = int(time.mktime((lt.tm_year + 1, mo, day, 0, 0, 0, 0, 0, -1)))
            return ts
        except Exception:
            return 0
    return 0


class Agenda:
    def __init__(self, store: Store = None, llm: LLM = None, verbose: bool = True):
        self.s = store or Store(verbose=False)
        self.llm = llm or LLM(model=MODEL_FAST)
        self.verbose = verbose
        self.s.con.executescript(SCHEMA)
        self.s.con.commit()

    def _log(self, m):
        if self.verbose:
            print(m, flush=True)

    # ---------------- 抽取 ----------------

    def _chunk_messages(self, chat_id: str, days: int = 0, chunk: int = 40):
        """把某个会话的消息切成连续块（保留对话上下文才能判准）。"""
        where = ["chat_id=?", "kind='text'"]
        args = [chat_id]
        if days:
            where.append("t >= ?")
            args.append(int(time.time()) - days * 86400)
        sql = ("select local_id,t,direction,sender_name,text from messages "
               f"where {' and '.join(where)} order by local_id")
        rows = list(self.s.con.execute(sql, args))
        for i in range(0, len(rows), chunk):
            yield rows[i:i + chunk]

    def extract_chat(self, chat_id: str, days: int = 0, max_chunks: int = 0,
                     chunk: int = 40) -> dict:
        """对单个会话做抽取。返回统计。"""
        nm = self.s.con.execute("select name from chats where chat_id=?",
                                (chat_id,)).fetchone()
        chat_name = nm[0] if nm else chat_id[:16]
        t0 = time.time()
        n_chunks = n_new = n_dup = 0
        n_skip_ref = n_skip_3rd = n_skip_sched = n_skip_mood = 0
        for block in self._chunk_messages(chat_id, days=days, chunk=chunk):
            if len(block) < 6:
                continue
            n_chunks += 1
            if max_chunks and n_chunks > max_chunks:
                break
            lines = []
            for i, (lid, t, d, sn, tx) in enumerate(block):
                who = "我" if d == "out" else (sn or "对方")
                lines.append(f"[{i}] {time.strftime('%m-%d %H:%M', time.localtime(t))} "
                             f"{who}: {(tx or '')[:100]}")
            user = (f"会话：{chat_name}\n\n" + "\n".join(lines))
            try:
                arr = self.llm.chat_json(EXTRACT_SYS, user,
                                         model=MODEL_FAST, max_tokens=2500)
            except Exception as e:
                self._log(f"    ! 抽取失败 {type(e).__name__}: {e}")
                continue
            if not isinstance(arr, list):
                continue
            for it in arr:
                if not isinstance(it, dict):
                    continue
                what = str(it.get("what") or "").strip()
                if not what or len(what) > 40:
                    continue
                quote = str(it.get("quote") or "").strip()
                kind = str(it.get("type") or "")[:12]
                who = str(it.get("who") or "")[:8]
                idx = it.get("msg_index")
                try:
                    idx = int(idx)
                except (TypeError, ValueError):
                    idx = 0
                idx = max(0, min(idx, len(block) - 1))
                lid, ts, d, sn, tx = block[idx]
                raw_text = tx or ""

                # ---- 过滤规则（都是实测踩出来的）----
                # a) 引用别人的话不能算说话人自己的承诺
                if raw_text.startswith("[引用]"):
                    n_skip_ref += 1
                    continue
                # b) 对第三方的承诺不属于当前会话双方
                if _maybe_third_party(quote) or _maybe_third_party(raw_text[:40]):
                    n_skip_3rd += 1
                    continue
                # c) 纯陈述自己日程的（对方说他自己的事）不是约定
                if _looks_like_own_schedule(raw_text):
                    n_skip_sched += 1
                    continue
                # d) 抱怨/情绪表达不是可执行的请求
                if _is_complaint(what) or _is_complaint(quote):
                    n_skip_mood += 1
                    continue
                # e) 只保留"我"或"共同"负义务的，才算**我的**待办；
                #    对方欠我的另存一类，避免混进我的待办清单
                if who == "对方":
                    kind = "their_" + (kind or "task")

                fp = _fp(chat_id, what)
                cur = self.s.con.execute(
                    "insert or ignore into agenda"
                    "(fp,chat_id,chat_name,local_id,t,day,kind,who,what,when_text,"
                    "when_t,quote,created) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fp, chat_id, chat_name, lid, ts,
                     time.strftime("%Y-%m-%d", time.localtime(ts)),
                     kind, who, what,
                     str(it.get("when") or "")[:30],
                     parse_when(str(it.get("when") or ""), ts),
                     (quote or raw_text)[:120],
                     time.strftime("%Y-%m-%d %H:%M:%S")))
                if cur.rowcount:
                    n_new += 1
                else:
                    n_dup += 1
            self.s.con.commit()
        dt = time.time() - t0
        self._log(f"  {chat_name[:20]:<22} 块 {n_chunks:>3}  新增 {n_new:>4}  "
                  f"重复 {n_dup:>4}  过滤(引用{n_skip_ref}/第三方{n_skip_3rd}/日程{n_skip_sched}/情绪{n_skip_mood})  {dt:.0f}s")
        return {"chat": chat_name, "chunks": n_chunks, "new": n_new, "dup": n_dup,
                "skipped": {"quote": n_skip_ref, "third_party": n_skip_3rd,
                            "own_schedule": n_skip_sched, "mood": n_skip_mood}}

    def extract_all(self, days: int = 180, min_msgs: int = 8, max_chats: int = 0,
                    chat_id: str = "", max_chunks: int = 120,
                    group_chunks: int = 40) -> dict:
        """批量抽取。

        ⚠️ 参数怎么选（实测定的）：
          单块耗时约 0.64 秒（deepseek-chat），所以"块数"就是时间。

          · days=180 —— 只看近半年（更久远的约定基本没意义）
          · max_chunks=120 —— 单聊上限
          · group_chunks=40 —— **群聊上限更小**：实测"杭州家教群"726 块抽出 0 条
            （全是通知/广告），为它花 8 分钟纯属浪费。
            约定主要发生在单聊里，群聊给少量配额做兜底就够了。
          当前规模 ≈ 1500 块 ≈ **17 分钟** ≈ ¥0.3。
        """
        if chat_id:
            chats = [(chat_id, "", 0)]
        else:
            q = ("select chat_id,name,is_group from chats where n_total>=? "
                 "order by n_total desc")
            chats = list(self.s.con.execute(q, (min_msgs,)))
            if max_chats:
                chats = chats[:max_chats]
        self._log(f"[*] 抽出约定：{len(chats)} 个会话 · 最近 {days} 天 · "
                  f"单聊上限 {max_chunks} 块 / 群聊上限 {group_chunks} 块")
        tot = {"chats": 0, "new": 0, "chunks": 0, "skipped": 0}
        t_start = time.time()
        for cid, _nm, is_grp in chats:
            mc = group_chunks if is_grp else max_chunks
            r = self.extract_chat(cid, days=days, max_chunks=mc)
            tot["chats"] += 1
            tot["new"] += r["new"]
            tot["chunks"] += r["chunks"]
            sk = r.get("skipped") or {}
            tot["skipped"] += sum(sk.values()) if isinstance(sk, dict) else 0
        tot["cost"] = self.llm.cost()
        tot["seconds"] = round(time.time() - t_start, 1)
        self._log(f"[+] 抽取完成：{tot['chats']} 会话 / {tot['chunks']} 块 / "
                  f"新增 {tot['new']} 条 · 过滤 {tot['skipped']} 条 · "
                  f"{tot['seconds']:.0f}s · ¥{tot['cost']['cost']:.4f}")
        return tot

    # ---------------- 查询 ----------------

    def list_open(self, days: int = 0,
                  kinds: tuple = ("promise", "task", "plan", "request"),
                  chat_id: str = "", limit: int = 100,
                  include_theirs: bool = False) -> list:
        """列出**我的**待办。默认不含 their_*（对方欠我的），那是另一类。"""
        where, args = ["status='open'"], []
        ks = list(kinds)
        if include_theirs:
            ks += ["their_" + k for k in kinds]
        if ks:
            where.append("kind in (%s)" % ",".join("?" * len(ks)))
            args += ks
        if days:
            where.append("t >= ?")
            args.append(int(time.time()) - days * 86400)
        if chat_id:
            where.append("chat_id=?")
            args.append(chat_id)
        sql = ("select id,t,day,chat_name,kind,who,what,when_text,when_t,quote,chat_id,local_id "
               f"from agenda where {' and '.join(where)} order by t desc limit ?")
        args.append(limit)
        out = []
        for r in self.s.con.execute(sql, args):
            out.append({"id": r[0], "t": r[1], "day": r[2], "chat": r[3],
                        "kind": r[4], "who": r[5], "what": r[6],
                        "when_text": r[7], "when_t": r[8], "quote": r[9],
                        "chat_id": r[10], "local_id": r[11],
                        "overdue": bool(r[8] and r[8] < time.time())})
        return out

    def stats(self) -> dict:
        c = self.s.con
        tot = c.execute("select count(*) from agenda").fetchone()[0]
        if not tot:
            return {"total": 0}
        kinds = dict(c.execute("select kind,count(*) from agenda group by kind"))
        status = dict(c.execute("select status,count(*) from agenda group by status"))
        return {"total": tot, "kinds": kinds, "status": status}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="约定挖掘机")
    ap.add_argument("cmd", choices=["extract", "list", "stats", "test-unit"])
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--chat", default="")
    ap.add_argument("--max-chats", type=int, default=0)
    ap.add_argument("--max-chunks", type=int, default=120,
                    help="每个会话最多抽多少块（1 块≈0.64秒）")
    ap.add_argument("--limit", type=int, default=30)
    a = ap.parse_args()

    ag = Agenda()
    if a.cmd == "test-unit":
        print("=== parse_when 单测 ===")
        base = int(time.mktime((2026, 9, 19, 12, 0, 0, 0, 0, -1)))
        for s in ["明天", "后天", "周五之前", "下周一", "3月5日", "周末", "随便什么时候"]:
            ts = parse_when(s, base)
            print("  %-8s -> %s" % (s, time.strftime("%Y-%m-%d", time.localtime(ts))
                                    if ts else "（解析不了）"))
        print()
        print("=== 指纹去重 ===")
        print("  ", _fp("c1", "把报告发给对方"), _fp("c1", "把报告发给对方！"),
              "相同" if _fp("c1", "把报告发给对方") == _fp("c1", "把报告发给对方！") else "不同")
    elif a.cmd == "extract":
        ag.extract_all(days=a.days, max_chats=a.max_chats, chat_id=a.chat,
                       max_chunks=a.max_chunks)
    elif a.cmd == "list":
        for x in ag.list_open(days=a.days, chat_id=a.chat, limit=a.limit):
            flag = " ⏰已过期" if x["overdue"] else ""
            print(f"  [{x['day']}] {x['chat'][:16]:<18} {x['kind']:<8} "
                  f"{x['what'][:34]:<36} {x['when_text'] or ''}{flag}")
    print()
    print("统计:", ag.stats())
