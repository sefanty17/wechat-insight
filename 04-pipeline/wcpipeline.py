# -*- coding: utf-8 -*-
"""
wcpipeline.py — 业务线抽象流水线（04-pipeline 的核心）

═══════════════════════════════════════════════════════════════════
  设计目标：不写死行业，让"业务线"变成一份配置
═══════════════════════════════════════════════════════════════════
  同一套代码要能处理：
    · 家教业务 —— 抽学员、科目、年级、课时费、上课时间
    · 团购/生意 —— 抽商品、价格、成色、联系方式
    · 球局组织 —— 抽时间、场地、人数、水平要求
    · 招聘/求职 —— 抽岗位、薪资、要求
    · 群聊情报 —— 抽公告、活动、@我、需要跟进的

  做法：**模板（template）= 一组字段定义**，每个字段声明：
    · key      字段名
    · label    显示名
    · desc     给 LLM 的抽取说明（越具体越准）
    · type     text / enum / number / list
    · values   enum 的可选值
    · required 缺了是否算抽取失败

  引擎流程：
    1. 选会话（按消息量/时间窗/手动指定）
    2. 切块过 LLM，按模板抽字段 → JSON
    3. **按字段合并**：同一个实体在多条消息里出现要合并
       （比如同一学员的信息散在几条消息里）
    4. 落库（可反复跑，去重）
    5. 出表格 / 管理面板

  ⚠️ 关键难点是「合并」：家教信息往往一条消息一个字段，
     比如"学员四年级""科目数学""一节课200"，要能拼成一行。
     所以抽取时要求模型带上 entity（实体名），再按 entity 聚合。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import csv
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

from wcstore import Store  # noqa: E402, F401
from wcllm import LLM, MODEL_FAST  # noqa: E402

TEMPLATE_FILE = os.path.join(HERE, "templates.json")
DATA_DB = os.path.join(CORE, "chat.db")

SCHEMA = """
create table if not exists records (
  id        integer primary key autoincrement,
  fp        text unique,        -- 去重指纹
  tpl       text,               -- 模板 id
  entity    text,               -- 实体名（学员名/商品名/活动名…）
  chat_id   text,
  chat_name text,
  local_id  integer,
  t         integer,
  day       text,
  fields    text,               -- JSON: 抽到的字段
  quote     text,               -- 依据原文
  conf      real default 1.0,
  created   text
);
create index if not exists idx_rec_tpl on records(tpl);
create index if not exists idx_rec_ent on records(tpl,entity);
create index if not exists idx_rec_t   on records(t);
"""

# 用户处理状态等"我们自己加的标注"放在单独一张表里 ——
# 因为 records 是**抽取产物**，重抽会清空；而"我标记过已处理"必须留下来。
LABEL_SCHEMA = """
create table if not exists record_labels (
  tpl     text,
  fp      text,               -- 对应 records.fp
  status  text default 'new',  -- new / follow / done / ignore
  starred integer default 0,
  note    text,
  updated text,
  primary key (tpl, fp)
);
create index if not exists idx_rl_status on record_labels(tpl,status);
"""


# ==========================================================================
# 内置模板
# ==========================================================================

BUILTIN = [
    {
        "id": "tutor",
        "name": "家教 / 辅导业务",
        "icon": "📚",
        "desc": "从家长群、学生私聊里抽出学员信息、科目、课时费和上课时间，"
                "汇成一张可跟进的管理表",
        "entity_label": "学员/家长",
        "hint": "适合家教、辅导、培训类业务线",
        "fields": [
            {"key": "student", "label": "学员", "type": "text", "required": True,
             "desc": "学员的名字或称呼；没有名字就写家长的称呼，如「小明」「李妈妈」"},
            {"key": "grade", "label": "年级", "type": "text",
             "desc": "年级，如「四年级」「高二」「初三」；没有就留空"},
            {"key": "subject", "label": "科目", "type": "enum",
             "values": ["数学", "物理", "化学", "英语", "语文", "生物", "全科", "其他"],
             "desc": "要辅导的科目"},
            {"key": "price", "label": "课时费", "type": "text",
             "desc": "费用描述，如「200/小时」「一节课300」「面议」"},
            {"key": "schedule", "label": "时间", "type": "text",
             "desc": "上课时间安排，如「周末上午」「周二周四晚7点」"},
            {"key": "location", "label": "地点", "type": "text",
             "desc": "上课地点或区域，如「拱墅区」「线上」"},
            {"key": "contact", "label": "联系方式", "type": "text",
             "desc": "电话/微信号；**只有原文里明确出现才写**，不要编"},
            {"key": "status", "label": "状态", "type": "enum",
             "values": ["待联系", "已沟通", "已试课", "已成交", "已拒绝", "不明"],
             "desc": "从对话语气判断的当前进展；判断不了就写「不明」"},
        ],
    },
    {
        "id": "trade",
        "name": "买卖 / 团购",
        "icon": "🛒",
        "desc": "从交易群里抽出商品、价格、成色和联系方式",
        "entity_label": "商品",
        "hint": "适合二手交易、团购、带货",
        "fields": [
            {"key": "item", "label": "商品", "type": "text", "required": True,
             "desc": "商品名称，尽量具体（型号/规格）"},
            {"key": "price", "label": "价格", "type": "text",
             "desc": "价格，如「120」「120-150」「面议」"},
            {"key": "condition", "label": "成色", "type": "enum",
             "values": ["全新", "九成新", "八成新", "七成新及以下", "不明"],
             "desc": "新旧程度"},
            {"key": "seller", "label": "卖家", "type": "text",
             "desc": "卖家称呼"},
            {"key": "contact", "label": "联系方式", "type": "text",
             "desc": "**只有原文明确出现才写**"},
            {"key": "status", "label": "状态", "type": "enum",
             "values": ["在售", "已出", "已预订", "不明"],
             "desc": "是否还在"},
        ],
    },
    {
        "id": "activity",
        "name": "活动 / 球局组织",
        "icon": "🏸",
        "desc": "从运动群、活动群里抽出时间、场地、人数和水平要求",
        "entity_label": "活动",
        "hint": "适合球局、聚会、线下活动组织",
        "fields": [
            {"key": "what", "label": "活动", "type": "text", "required": True,
             "desc": "活动内容，如「羽毛球」「打牌」「剧本杀」"},
            {"key": "when", "label": "时间", "type": "text",
             "desc": "活动时间，保留原文说法，如「今晚8-10」「周六下午」"},
            {"key": "where", "label": "场地", "type": "text",
             "desc": "地点或场地号"},
            {"key": "people", "label": "人数", "type": "text",
             "desc": "人数或缺口，如「已有4人还缺2」「3=2」"},
            {"key": "level", "label": "水平要求", "type": "text",
             "desc": "对参与者的水平/资格要求"},
            {"key": "cost", "label": "费用", "type": "text",
             "desc": "AA 费用或说明"},
            {"key": "organizer", "label": "组织者", "type": "text",
             "desc": "发起人的称呼"},
        ],
    },
    {
        "id": "intel",
        "name": "群聊情报",
        "icon": "📡",
        "desc": "从几百条群消息里捞出真正重要的：@我的、公告、需要跟进的、"
                "和我相关的机会",
        "entity_label": "事项",
        "hint": "适合信息过载的群，把噪音过滤掉",
        "fields": [
            {"key": "what", "label": "事项", "type": "text", "required": True,
             "desc": "一句话说清是什么事，20字内"},
            {"key": "kind", "label": "类型", "type": "enum",
             "values": ["@我", "公告", "机会", "需要跟进", "重要通知", "其他"],
             "desc": "这条属于哪类"},
            {"key": "who", "label": "相关人", "type": "text",
             "desc": "提到的人"},
            {"key": "when", "label": "时间", "type": "text",
             "desc": "时间信息"},
            {"key": "action", "label": "要做什么", "type": "text",
             "desc": "如果需要我行动，写出具体动作；不需要则留空"},
            {"key": "urgency", "label": "紧急度", "type": "enum",
             "values": ["高", "中", "低"],
             "desc": "从措辞和时间判断"},
        ],
    },
    {
        "id": "job",
        "name": "求职 / 招聘",
        "icon": "💼",
        "desc": "从招聘群、HR 私聊里抽岗位、薪资和要求",
        "entity_label": "岗位",
        "hint": "适合找工作或招人",
        "fields": [
            {"key": "role", "label": "岗位", "type": "text", "required": True,
             "desc": "职位名称"},
            {"key": "company", "label": "公司", "type": "text", "desc": "公司或机构名"},
            {"key": "salary", "label": "薪资", "type": "text",
             "desc": "薪资范围，保留原文"},
            {"key": "require", "label": "要求", "type": "text",
             "desc": "学历/经验/技能要求"},
            {"key": "location", "label": "地点", "type": "text", "desc": "工作地点"},
            {"key": "contact", "label": "联系方式", "type": "text",
             "desc": "**只有原文明确出现才写**"},
            {"key": "status", "label": "状态", "type": "enum",
             "values": ["待投递", "已投递", "已沟通", "已面试", "已拒绝", "不明"],
             "desc": "进展"},
        ],
    },
]


def load_templates() -> list:
    """内置模板 + 用户自定义（自定义存 templates.json，同 id 会覆盖内置）。"""
    tpls = {t["id"]: dict(t) for t in BUILTIN}
    if os.path.exists(TEMPLATE_FILE):
        try:
            with open(TEMPLATE_FILE, encoding="utf-8") as f:
                for t in json.load(f):
                    if t.get("id"):
                        tpls[t["id"]] = t
        except (json.JSONDecodeError, OSError):
            pass
    return list(tpls.values())


def get_template(tid: str) -> dict | None:
    for t in load_templates():
        if t["id"] == tid:
            return t
    return None


def save_template(t: dict) -> dict:
    """保存自定义模板（不带内置 id 覆盖时就是新建）。"""
    t = dict(t)
    t.setdefault("id", "custom-" + hashlib.md5(
        str(time.time()).encode()).hexdigest()[:8])
    t.setdefault("name", t["id"])
    t.setdefault("icon", "⚙️")
    t.setdefault("entity_label", "条目")
    custom = []
    if os.path.exists(TEMPLATE_FILE):
        try:
            with open(TEMPLATE_FILE, encoding="utf-8") as f:
                custom = json.load(f)
        except (json.JSONDecodeError, OSError):
            custom = []
    custom = [x for x in custom if x.get("id") != t["id"]] + [t]
    with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
        json.dump(custom, f, ensure_ascii=False, indent=1)
    return t


# ==========================================================================
# Prompt 构造
# ==========================================================================

def build_prompt(tpl: dict) -> str:
    fields = "\n".join(
        f'  · {f["label"]}（键名 {f["key"]}，类型 {f["type"]}'
        + (f'，可选值：{"/".join(f["values"])}' if f.get("values") else "")
        + f'）：{f.get("desc","")}'
        + ("　【必填】" if f.get("required") else "")
        for f in tpl["fields"])
    return f"""你是一个信息抽取器，从微信聊天片段里抽取「{tpl['name']}」相关的结构化信息。

抽取字段：
{fields}

输出要求：
1. 只输出 JSON 数组，不要解释、不要 markdown 代码块。
2. 每个元素格式：
   {{"entity":"实体名（{tpl.get('entity_label','条目')}）",
     "fields":{{"字段键名":"值", ...}},
     "quote":"依据原文，25字内",
     "msg_index":依据本块第几条消息(从0开始)}}
3. **entity 是聚合的依据**：同一件事/同一个人在多条消息里的信息要合并成一条，
   值尽量写全（比如价格在另一条消息里提到，也要合进来）。
4. 字段值缺失就**不要包含这个键**，不要写"未知""无""空"。
5. **绝对不要编造**：原文没有的电话、价格、名字一律不写。
6. 没有可抽取的内容就返回 []，宁缺勿滥。
7. 广告、通知、表情、寒暄不算。

只输出 JSON 数组。"""


def _fp(tpl_id: str, entity: str, fields: dict) -> str:
    s = tpl_id + "|" + (entity or "")[:20] + "|" + json.dumps(
        fields, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(s.encode()).hexdigest()[:16]


# ==========================================================================
# 引擎
# ==========================================================================


# ==========================================================================
# 同义词扩展（语义搜索的轻量实现）
# ==========================================================================
#
# 用户模拟里的原话：
#   "群里有人写'有想去当家教的不，一小时 80'，也有人写'招个陪练'，
#     我搜'兼职'根本搜不到，因为它们字面上不含'兼职'两个字。这条信息等于丢了。"
#
# 思路：一个零依赖的同义词/上位词表。搜任一个词，就把**同一组**里的其他词
# 也一起匹配（OR）。比向量检索简单得多，但对这种垂直场景足够有效，
# 而且**可解释**（能告诉用户"我是按这些词扩展的"）。
# ⚠️ 结构说明（踩过一次坑）：
#   最初写成 {组名: [词...]}，然后"第一个包含该词的组"整组展开。
#   结果搜「家教」命中了「兼职」那个大组，把"日结/陪练/小时工"全带进来 ——
#   噪声大，用户也看不懂凭什么。
#   改成：显式写「组」，再**扁平索引**每个词到它所属的组。
#   这样「家教」只展开家教组，不串到「兼职」组。
SYN_GROUPS = [
    ["兼职", "招人", "招聘", "招个", "找人", "日结", "小时工", "临时工",
     "代课", "跑腿", "勤工", "实习", "内推", "招募", "名额"],
    ["家教", "辅导", "补课", "一对一", "上门教", "陪读", "作业辅导"],
    ["二手", "闲置", "便宜出", "低价", "清仓", "甩", "转", "出"],
    ["球局", "打球", "约球", "缺人", "少一人", "场地", "球友",
     "羽毛球", "乒乓球", "网球"],
    ["租房", "合租", "转租", "房源", "单间", "押一付", "找室友"],
    ["急", "马上", "今晚", "立刻", "尽快", "最后", "截止", "限时", "仅剩"],
    ["免费", "白送", "不要钱", "零元"],
    ["通知", "公告", "周知", "提醒", "变更", "取消", "延期"],
    ["机会", "内推", "开放", "报名"],
    ["求购", "想要", "收", "蹲", "有没有"],
]

# 扁平索引：词 → 它所在的组
_SYN_INDEX = {}
for _g in SYN_GROUPS:
    for _w in _g:
        _SYN_INDEX.setdefault(_w, _g)


def expand_query(q: str) -> dict:
    """把搜索词扩展成同义词组。

    返回 {"terms": 要匹配的词, "groups": [{"word","expanded"}]}
      · 每个词**只展开它自己所属的那一组**，不跨组串味
      · 没命中同义词表的（数字、专名）原样保留
    """
    q = (q or "").strip()
    if not q:
        return {"terms": [], "groups": []}
    words = [w for w in re.split(r"[\s,，、]+", q) if w]
    terms, groups = [], []
    for w in words:
        g = _SYN_INDEX.get(w)
        if g:
            groups.append({"word": w, "expanded": list(g)})
            terms.extend(g)
        else:
            terms.append(w)
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return {"terms": out, "groups": groups}


class Pipeline:
    def __init__(self, store: Store = None, llm: LLM = None, verbose: bool = True):
        self.s = store or Store(verbose=False)
        self.llm = llm or LLM(model=MODEL_FAST)
        self.verbose = verbose
        self.s.con.executescript(SCHEMA)
        self.s.con.executescript(LABEL_SCHEMA)
        self.s.con.commit()

    def _log(self, m):
        if self.verbose:
            print(m, flush=True)

    # ---------- 选会话 ----------

    def pick_chats(self, template_id: str, chats: list | None = None,
                   pattern: str = "", days: int = 180, min_msgs: int = 20,
                   top: int = 10) -> list:
        """选要扫的会话。三种方式：显式指定 / 名字模糊匹配 / 按消息量取前 N。

        ⚠️ chats 里的元素可能是 **chat_id**（界面多选传过来的），也可能是
        **名字**（命令行手输）。原来用「首字符像不像 wxid_/1/5」来猜，
        很脆 —— 群 id 是 `xxx@chatroom`、好友 id 可能是任意串，都会猜错。
        改成：**先按 id 精确查，查不到再按名字模糊查**，两种都支持。
        """
        if chats:
            out, seen = [], set()
            for c in chats:
                r = self.s.con.execute(
                    "select chat_id,name,is_group from chats where chat_id=?",
                    (c,)).fetchone()
                if not r:      # 不是 id，那就当名字模糊找（取消息量最大的那个）
                    hits = self.s.chat_id_by_name(c)
                    if hits:
                        h = hits[0]
                        r = (h["chat_id"], h["name"], 1 if h["is_group"] else 0)
                if r and r[0] not in seen:
                    seen.add(r[0])
                    out.append((r[0], r[1], bool(r[2])))
            return out
        cut = int(time.time()) - days * 86400
        where = ["c.n_total>=?"]
        args = [min_msgs]
        if pattern:
            where.append("c.name like ?")
            args.append(f"%{pattern}%")
        sql = (f"select c.chat_id,c.name,c.is_group,count(m.id) n from chats c "
               f"join messages m on m.chat_id=c.chat_id and m.t>=? "
               f"where {' and '.join(where)} group by c.chat_id "
               f"having n>=? order by n desc limit ?")
        rows = self.s.con.execute(sql, [cut] + args + [min_msgs, top]).fetchall()
        return [(r[0], r[1], bool(r[2])) for r in rows]

    # ---------- 抽取 ----------

    def run(self, template_id: str, chats: list | None = None, pattern: str = "",
            days: int = 180, top: int = 8, chunk: int = 60,
            max_chunks: int = 60, clear: bool = False) -> dict:
        tpl = get_template(template_id)
        if not tpl:
            raise SystemExit(f"没有模板 {template_id!r}")
        if clear:
            self.s.con.execute("delete from records where tpl=?", (template_id,))
            self.s.con.commit()
            self._log(f"[*] 已清空模板 {template_id} 的旧记录")

        targets = self.pick_chats(template_id, chats=chats, pattern=pattern,
                                  days=days, top=top)
        self._log(f"[*] 模板《{tpl['name']}》→ 扫描 {len(targets)} 个会话，"
                  f"最近 {days} 天，每会话最多 {max_chunks} 块")
        sys_p = build_prompt(tpl)
        cut = int(time.time()) - days * 86400
        n_new = n_dup = n_chunk = 0
        t0 = time.time()

        for cid, cname, is_grp in targets:
            # ⚠️ 从**最新**往回取，块内再倒回正序。
            #    原来 `order by local_id`（从最早）+ max_chunks 截断，
            #    结果永远只处理最早的几十块，**近期的一条都抽不到** ——
            #    实测 records 最新日期停在 5 月，而库里有到 9 月；
            #    用户反馈"天天有消息的群却看不到今天的情况"就是这个 bug。
            rows = list(self.s.con.execute(
                "select local_id,t,direction,sender_name,text from messages "
                "where chat_id=? and kind='text' and t>=? order by local_id desc",
                (cid, cut)))
            used = 0
            for i in range(0, len(rows), chunk):
                if used >= max_chunks:
                    break
                # 块内恢复时间正序（模型要按对话顺序读）
                block = sorted(rows[i:i + chunk], key=lambda r: r[0])
                if len(block) < 4:
                    continue
                used += 1
                n_chunk += 1
                lines = []
                for j, (lid, t, d, sn, tx) in enumerate(block):
                    who = "我" if d == "out" else (sn or "对方")
                    lines.append(f"[{j}] {time.strftime('%m-%d %H:%M', time.localtime(t))} "
                                 f"{who}: {(tx or '')[:110]}")
                user = f"会话：{cname}\n\n" + "\n".join(lines)
                try:
                    arr = self.llm.chat_json(sys_p, user, model=MODEL_FAST,
                                             max_tokens=3000)
                except Exception as e:
                    self._log(f"    ! 抽取失败 {type(e).__name__}: {e}")
                    continue
                if not isinstance(arr, list):
                    continue
                for it in arr:
                    if not isinstance(it, dict):
                        continue
                    ent = str(it.get("entity") or "").strip()[:40]
                    flds = it.get("fields")
                    if not ent or not isinstance(flds, dict):
                        continue
                    # 只保留模板定义的字段，且过滤空值
                    clean = {}
                    for f in tpl["fields"]:
                        v = flds.get(f["key"])
                        if v is None:
                            continue
                        vs = str(v).strip()
                        if not vs or vs in ("未知", "无", "空", "null", "None", "-", "不明确", "不明"):
                            if f["type"] != "enum" or vs not in ("不明",):
                                continue
                        if f.get("values") and vs not in f["values"] and f["type"] == "enum":
                            # enum 值不在候选里 → 保留但标记，便于发现问题
                            pass
                        clean[f["key"]] = vs[:80]
                    if not clean:
                        continue
                    idx = it.get("msg_index")
                    try:
                        idx = max(0, min(int(idx), len(block) - 1))
                    except (TypeError, ValueError):
                        idx = 0
                    lid, ts, d, sn, tx = block[idx]
                    fp = _fp(template_id, ent, clean)
                    cur = self.s.con.execute(
                        "insert or ignore into records"
                        "(fp,tpl,entity,chat_id,chat_name,local_id,t,day,fields,quote,created)"
                        " values(?,?,?,?,?,?,?,?,?,?,?)",
                        (fp, template_id, ent, cid, cname, lid, ts,
                         time.strftime("%Y-%m-%d", time.localtime(ts)),
                         json.dumps(clean, ensure_ascii=False),
                         str(it.get("quote") or tx or "")[:140],
                         time.strftime("%Y-%m-%d %H:%M:%S")))
                    if cur.rowcount:
                        n_new += 1
                    else:
                        n_dup += 1
                self.s.con.commit()
            self._log(f"    {cname[:20]:<22} {len(rows):>6} 条 → {used:>3} 块")

        cost = self.llm.cost()
        res = {"ok": True, "template": template_id, "chats": len(targets),
               "chunks": n_chunk, "new": n_new, "dup": n_dup,
               "seconds": round(time.time() - t0, 1), "cost": cost["cost"]}
        self._log(f"[+] 完成：{n_chunk} 块 / 新增 {n_new} 条 / 重复 {n_dup} · "
                  f"{res['seconds']:.0f}s · ¥{res['cost']:.4f}")
        return res

    # ---------- 合并 ----------

    def set_label(self, tpl: str, fp: str, status: str = None,
                  starred: bool = None, note: str = None) -> dict:
        """标记一条记录的处理状态/星标。"""
        row = self.s.con.execute(
            "select status,starred,note from record_labels where tpl=? and fp=?",
            (tpl, fp)).fetchone()
        cur_status = row[0] if row else "new"
        cur_star = row[1] if row else 0
        cur_note = row[2] if row else ""
        self.s.con.execute(
            "insert into record_labels(tpl,fp,status,starred,note,updated) "
            "values(?,?,?,?,?,?) on conflict(tpl,fp) do update set "
            "status=excluded.status, starred=excluded.starred, "
            "note=excluded.note, updated=excluded.updated",
            (tpl, fp,
             status if status is not None else cur_status,
             1 if (starred if starred is not None else cur_star) else 0,
             note if note is not None else cur_note,
             time.strftime("%Y-%m-%d %H:%M:%S")))
        self.s.con.commit()
        return {"ok": True, "fp": fp, "status": status or cur_status,
                "starred": bool(starred if starred is not None else cur_star)}

    def labels(self, tpl: str) -> dict:
        """取这个模板下所有标注：{fp: {status, starred}}。"""
        out = {}
        for fp, st, star, note in self.s.con.execute(
                "select fp,status,starred,note from record_labels where tpl=?",
                (tpl,)):
            out[fp] = {"status": st or "new", "starred": int(star or 0),
                       "note": note or ""}
        return out

    def merge(self, template_id: str) -> list:
        """按 entity 合并：同一个实体在不同消息里抽到的字段拼成一行。

        这是最有价值的一步 —— 家教信息常常散在多条消息里
        （"学员四年级" / "科目数学" / "一节课200"），不合并就是三条碎记录。
        """
        tpl = get_template(template_id) or {"fields": []}
        rows = list(self.s.con.execute(
            "select id,entity,chat_id,chat_name,t,day,fields,quote,fp,local_id "
            "from records where tpl=? order by t desc", (template_id,)))
        groups = {}
        for rid, ent, cid, cname, t, day, fjs, quote, fp, lid in rows:
            key = (ent or "").strip() or f"未命名-{rid}"
            g = groups.setdefault(key, {"entity": key, "fields": {}, "chats": set(),
                                        "first_day": day, "last_day": day,
                                        "quotes": [], "ids": [], "fps": [],
                                        "t0": t, "local_id": None})
            try:
                f = json.loads(fjs or "{}")
            except json.JSONDecodeError:
                f = {}
            for k, v in f.items():
                # 后面的（更新的）覆盖前面的；但空值不覆盖
                if v:
                    g["fields"][k] = v
            g["chats"].add(cname)
            g["ids"].append(rid)
            if fp:
                g["fps"].append(fp)
            if lid:
                g["local_id"] = lid
            if day:
                g["first_day"] = min(g["first_day"] or day, day)
                g["last_day"] = max(g["last_day"] or day, day)
            if quote and len(g["quotes"]) < 3:
                g["quotes"].append(quote)
        out = []
        for g in groups.values():
            g["chats"] = sorted(g["chats"])
            g["n_sources"] = len(g["ids"])
            # 必填字段缺失的标记出来
            missing = [f["label"] for f in tpl["fields"]
                       if f.get("required") and not g["fields"].get(f["key"])]
            g["missing"] = missing
            g["fp"] = g["fps"][0] if g["fps"] else ""
            g["chat_id"] = g.get("chat_id") or ""
            out.append(g)
        labels = self.labels(template_id)
        for g in out:
            lb = labels.get(g.get("fp") or "", {})
            g["status"] = lb.get("status", "new")
            g["starred"] = bool(lb.get("starred"))
            g["note"] = lb.get("note", "")
        # 星标置顶，其次按来源数、时间
        out.sort(key=lambda x: (0 if x.get("starred") else 1,
                                -x["n_sources"], x["last_day"] or ""))
        return out

    # ---------- 导出 ----------

    def export_csv(self, template_id: str, path: str = "") -> str:
        tpl = get_template(template_id) or {"fields": [], "name": template_id}
        data = self.merge(template_id)
        if not path:
            path = os.path.join(HERE, f"{template_id}-{time.strftime('%Y%m%d')}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([tpl.get("entity_label", "条目")]
                       + [x["label"] for x in tpl["fields"]]
                       + ["来源会话", "首次", "最近", "条数", "依据"])
            for g in data:
                w.writerow([g["entity"]]
                           + [g["fields"].get(x["key"], "") for x in tpl["fields"]]
                           + ["；".join(g["chats"][:3]), g["first_day"], g["last_day"],
                              g["n_sources"], " / ".join(g["quotes"][:2])])
        return path

    def stats(self, template_id: str = "") -> dict:
        c = self.s.con
        if template_id:
            n = c.execute("select count(*) from records where tpl=?",
                          (template_id,)).fetchone()[0]
            ents = c.execute("select count(distinct entity) from records where tpl=?",
                             (template_id,)).fetchone()[0]
            return {"records": n, "entities": ents}
        rows = c.execute("select tpl,count(*),count(distinct entity) from records "
                         "group by tpl").fetchall()
        return {r[0]: {"records": r[1], "entities": r[2]} for r in rows}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="业务线流水线")
    ap.add_argument("cmd", nargs="?", default="list",
                    choices=["list", "run", "show", "csv", "templates"])
    ap.add_argument("--tpl", default="")
    ap.add_argument("--pattern", default="", help="按会话名模糊匹配")
    ap.add_argument("--chat", action="append", default=[], help="指定会话（可多次）")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--max-chunks", type=int, default=60)
    ap.add_argument("--clear", action="store_true")
    a = ap.parse_args()

    p = Pipeline()
    if a.cmd == "templates":
        for t in load_templates():
            print(f"  {t.get('icon','')} {t['id']:<10} {t['name']}")
            print(f"      {t.get('desc','')}")
            print(f"      字段: {', '.join(f['label'] for f in t['fields'])}")
        raise SystemExit(0)
    if a.cmd == "list":
        st = p.stats()
        if not st:
            print("还没有任何抽取结果。先跑：python wcpipeline.py run --tpl tutor")
        for k, v in st.items():
            t = get_template(k) or {}
            print(f"  {t.get('icon','')} {k:<10} {v['records']:>5} 条记录 / "
                  f"{v['entities']:>4} 个{t.get('entity_label','条目')}")
        raise SystemExit(0)
    if not a.tpl:
        raise SystemExit("需要 --tpl 指定模板（用 templates 子命令看有哪些）")
    if a.cmd == "run":
        p.run(a.tpl, chats=a.chat or None, pattern=a.pattern, days=a.days,
              top=a.top, max_chunks=a.max_chunks, clear=a.clear)
    tpl = get_template(a.tpl) or {"fields": [], "entity_label": "条目", "name": a.tpl}
    data = p.merge(a.tpl)
    if a.cmd == "csv":
        print("已导出:", p.export_csv(a.tpl))
    else:
        print(f"\n《{tpl['name']}》合并后 {len(data)} 个{tpl.get('entity_label','条目')}\n")
        hdr = [tpl.get("entity_label", "条目")] + [f["label"] for f in tpl["fields"]]
        print("  " + " | ".join(h[:8].ljust(8) for h in hdr) + " | 来源")
        print("  " + "-" * 78)
        for g in data[:40]:
            row = [g["entity"][:8].ljust(8)]
            for f in tpl["fields"]:
                row.append(str(g["fields"].get(f["key"], "-"))[:8].ljust(8))
            print("  " + " | ".join(row) + f" | {g['n_sources']}条 "
                  + ("⚠缺" + ",".join(g["missing"]) if g["missing"] else ""))
