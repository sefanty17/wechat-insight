# -*- coding: utf-8 -*-
"""
wcpersona.py — 人设模拟 + 关系分析 + 情感建议（03-persona 的智能层）

═══════════════════════════════════════════════════════════════════
  设计原则（很重要）
═══════════════════════════════════════════════════════════════════
  1. **先给数据，再给解读，最后才给建议**。顺序不能反。
     用户需要能看到"你凭什么这么说"。
  2. **必须加载 kb/relationship_methodology.md**，所有建议都要能指回
     某个框架（Gottman / 依恋 / NVC），不许自由发挥。
  3. **不给决定**。不说"该不该表白""要不要分手"。
     只给「沟通方式」层面的建议 + 几种可能的解释。
  4. **人设是「这个人在这段关系里的样子」，不是人格诊断**。
     措辞上必须体现这一点。
═══════════════════════════════════════════════════════════════════

用法：
  python wcpersona.py "jang-jawan."                 # 完整分析
  python wcpersona.py "jang-jawan." --days 90        # 只看最近 90 天
  python wcpersona.py --list
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "00-core")
KB = os.path.join(ROOT, "kb")
for p in (CORE, HERE, KB):
    if p not in sys.path:
        sys.path.insert(0, p)

from wcstore import Store  # noqa: E402
from wcllm import LLM, MODEL_FAST, MODEL_DEEP  # noqa: E402
from wcmetrics import RelationMetrics  # noqa: E402

METHODOLOGY = os.path.join(KB, "relationship_methodology.md")

PERSONA_SYS = """你在帮用户理解「某个人在这段聊天关系里是什么样」。

用户会给你：
  · 一份关系分析方法论（判断依据）
  · 这个人的量化指标（客观算出来的）
  · 这个人的聊天原话样本
  · 这个人的朋友圈内容（如果有）

请输出**这个人的互动人设**。要求：

1. **严格基于给定材料**，不要编造材料里没有的性格特征。
2. 分成这几块输出（用 markdown）：
   ### 互动风格
   这个人怎么说话、怎么表达关心、怎么表达不满。引用具体原话作证据。
   ### 在意什么
   从话题分布看，这个人反复关注的是什么（家人/工作/爱好/某个人…）。
   每条都要有依据。
   ### 情绪模式
   开心/烦躁/冷淡分别怎么表现。什么话题会让他变短、什么话题会让他变长。
   ### 沟通节奏
   回复快慢、主动性的特征。**注意不要说"他不在乎你"这类结论**，
   只说观察到的事实（比如"工作时段回复明显变慢"）。
   ### 相处建议（3~5 条）
   每条必须：
     · 指明依据（引用上面的哪个指标，或方法论里的哪个框架）
     · 给**具体可以怎么说**的一句话示例
     · 分「保守/中性/主动」三档供选择

3. **禁止**：
   · 不给"该不该表白/分手/结婚"这类决定
   · 不预测关系结果
   · 不用"他肯定…""说明他不爱你"这种断言
   · 不做人格或心理诊断
4. 拿不准的地方**明确说"材料里看不出来"**。
5. 语气：像一个懂心理学但说话直接的朋友，不是咨询师口吻。"""


class Persona:
    def __init__(self, store: Store = None, llm: LLM = None, verbose: bool = True):
        self.s = store or Store(verbose=False)
        self.m = RelationMetrics(self.s)
        self.llm = llm or LLM(model=MODEL_FAST)
        self.verbose = verbose

    def _log(self, x):
        if self.verbose:
            print(x, flush=True)

    # ---------------- 朋友圈 ----------------

    def moments(self, wxid: str, limit: int = 40) -> list:
        """读这个人的朋友圈（本机缓存过的）。

        微信 4.x 的朋友圈在 sns.db 里，SnsTimeLine.content 是 XML。
        ⚠️ 只有**本机缓存过的**动态（你刷到过的），不是全量历史。
        """
        import sqlite3
        import wcstat
        out = []
        try:
            db = wcstat.find_db_dir()
            keys = wcstat.load_keys()
            rel = "sns\\sns.db"
            key = (keys.get(rel) or {}).get("enc_key")
            if not key:
                return []
            tmp = os.path.join(wcstat.TMP_DIR, "sns", "sns.db")
            if not os.path.exists(tmp):
                wcstat.decrypt_db(os.path.join(db, "sns", "sns.db"),
                                  bytes.fromhex(key), tmp)
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            for user, content in con.execute(
                    "select user_name, content from SnsTimeLine "
                    "where user_name=? limit ?", (wxid, limit)):
                if not content:
                    continue
                desc = re.search(r"<contentDesc>(.*?)</contentDesc>", content, re.S)
                ts = re.search(r"<createTime>(\d+)</createTime>", content)
                txt = (desc.group(1) if desc else "").strip()
                if txt:
                    out.append({"t": int(ts.group(1)) if ts else 0, "text": txt[:200]})
            con.close()
        except Exception as e:
            self._log(f"    ! 朋友圈读取失败: {type(e).__name__}: {e}")
        out.sort(key=lambda x: x["t"])
        return out

    # ---------------- 样本 ----------------

    def samples(self, chat_id: str, days: int = 0, n_each: int = 25) -> dict:
        """取双方的原话样本：多样本、覆盖不同长度和时段。"""
        msgs = self.m.messages(chat_id, days)
        inc = [m for m in msgs if m["dir"] == "in" and 2 <= len(m["text"]) <= 60]
        out = [m for m in msgs if m["dir"] == "out" and 2 <= len(m["text"]) <= 60]
        import random
        random.seed(42)
        pick = lambda a: [{"t": x["day"], "text": x["text"]} for x in
                          random.sample(a, min(n_each, len(a)))] if a else []
        return {"theirs": pick(inc), "mine": pick(out)}

    # ---------------- 主流程 ----------------

    def analyze(self, chat_id: str, days: int = 0, with_moments: bool = True,
                use_deep_model: bool = False) -> dict:
        t0 = time.time()
        nm = self.s.con.execute("select name,is_group from chats where chat_id=?",
                                (chat_id,)).fetchone()
        name = nm[0] if nm else chat_id[:16]
        is_group = bool(nm[1]) if nm else False
        self._log(f"[1] 计算关系指标：{name}")
        met = self.m.all(chat_id, days=days)

        self._log("[2] 取聊天样本…")
        smp = self.samples(chat_id, days)

        moments = []
        if with_moments and not is_group:
            self._log("[3] 读朋友圈…")
            moments = self.moments(chat_id)
            self._log(f"    拿到 {len(moments)} 条")

        self._log("[4] 加载方法论…")
        method = ""
        if os.path.exists(METHODOLOGY):
            with open(METHODOLOGY, encoding="utf-8") as f:
                method = f.read()
        self._log(f"    {len(method)} 字符")

        self._log("[5] LLM 生成人设与建议…")
        b = met["basics"]
        fh = met["four_horsemen"]
        mr = met["magic_ratio"]
        bi = met["bids"]
        lat = met["latency"]
        at = met["attachment"]

        metrics_text = f"""【量化指标】（窗口：{'全部' if not days else f'最近{days}天'}）
消息量：共 {b['total']} 条文本，我发出 {b['out']}，对方 {b['in']}，跨度 {b['days']} 天
平均长度：我 {b['avg_len_out']} 字，对方 {b['avg_len_in']} 字
主动性：我开启话题 {b['init_mine']} 天，对方 {b['init_their']} 天（我/对方 = {b['init_ratio']}）

Gottman 正面/负面互动比：{mr['positive']} : {mr['negative']} = {mr['ratio']}（{mr['verdict']}）
四骑士出现率：批评 {fh['criticism']['rate']}% / 蔑视 {fh['contempt']['rate']}% /
  防御 {fh['defensiveness']['rate']}% / 冷战信号 {fh['stonewalling']['rate']}%
  批评样例：{fh['criticism']['eg']}
  蔑视样例：{fh['contempt']['eg']}
  防御样例：{fh['defensiveness']['eg']}

情感邀约回应率 turn_rate：{bi['turn_rate']}%（{bi['turned_towards']}/{bi['bids']}）
  错过的邀约样例：{bi['missed_examples']}
回复中位间隔：我 {lat['mine'].get('median_min')} 分钟 / 对方 {lat['theirs'].get('median_min')} 分钟
  我的回复分布：{lat['mine_split']}
  对方回复分布：{lat['theirs_split']}

深度话题占比：{met['deep_topic']['ratio']}%
依恋信号：焦虑信号 {at['anxiety_signal']['n']} 条（{at['anxiety_signal']['rate']}%）
  样例：{at['anxiety_signal']['eg']}
  回避信号：亲密话题{at['avoidance_signal']['deep_avg_len']}字 vs 普通话题
  {at['avoidance_signal']['normal_avg_len']}字（比值 {at['avoidance_signal']['ratio']}）
  {at['avoidance_signal']['note']}

最近 12 周趋势（消息量/我发出/平均长度/正面占比/活跃天）：
{json.dumps(met['trends'][-8:], ensure_ascii=False)}"""

        theirs = "\n".join(f"[{x['t']}] {x['text']}" for x in smp["theirs"][:30])
        mine = "\n".join(f"[{x['t']}] {x['text']}" for x in smp["mine"][:20])
        mom = ""
        if moments:
            mom = ("\n\n【这个人的朋友圈】（本机缓存过的，不是全量）\n"
                   + "\n".join(f"[{time.strftime('%m-%d', time.localtime(x['t']))}] "
                               f"{x['text']}" for x in moments[:30]))

        user = (f"分析对象：{name}\n"
                f"{metrics_text}\n\n"
                f"【这个人说过的原话样本】\n{theirs}\n\n"
                f"【我说过的原话样本（用于对比）】\n{mine}{mom}\n\n"
                f"请按方法论输出这个人设与相处建议。")

        model = MODEL_DEEP if use_deep_model else MODEL_FAST
        try:
            text = self.llm.chat(
                [{"role": "system", "content": METHODOLOGY_PROMPT(method)},
                 {"role": "user", "content": user}],
                model=model, max_tokens=6000, temperature=0.4)
        except Exception as e:
            text = f"（生成失败：{type(e).__name__}: {e}）"

        return {"ok": True, "name": name, "chat_id": chat_id, "is_group": is_group,
                "metrics": met, "samples": smp, "moments": moments,
                "persona": text, "seconds": round(time.time() - t0, 1),
                "cost": self.llm.cost(), "model": model}


def METHODOLOGY_PROMPT(method: str) -> str:
    return (PERSONA_SYS
            + "\n\n══════════ 关系分析方法论（你的判断依据）══════════\n"
            + (method[:9000] if method else "（方法论文件未找到，请只依据给定数据分析）"))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="人设模拟 + 关系分析")
    ap.add_argument("chat", nargs="?", default="")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-moments", action="store_true")
    ap.add_argument("--deep", action="store_true", help="用更强的模型（慢、贵）")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    s = Store(verbose=False)
    if a.list or not a.chat:
        from wcmetrics import candidate_chats
        print("可分析的会话：")
        for c in candidate_chats(s):
            print(f"  {c['name'][:24]:<26}{'群' if c['is_group'] else '单聊':<5}"
                  f"{c['n']:>7} 条")
        return

    hits = s.chat_id_by_name(a.chat)
    if not hits:
        raise SystemExit(f"找不到 {a.chat!r}")
    p = Persona(s)
    r = p.analyze(hits[0]["chat_id"], days=a.days,
                  with_moments=not a.no_moments, use_deep_model=a.deep)
    print("\n" + "=" * 70)
    print(r["persona"])
    print("=" * 70)
    print(f"\n{r['seconds']}s · {r['model']} · ¥{r['cost']['cost']:.4f} · "
          f"朋友圈 {len(r['moments'])} 条")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in r.items() if k != "metrics"}, f,
                      ensure_ascii=False, indent=1)
        print("已保存:", a.out)


if __name__ == "__main__":
    main()
