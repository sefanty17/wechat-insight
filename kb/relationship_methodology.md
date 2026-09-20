# 关系分析方法论 · Relationship Analysis Methodology

> 这是 **03-persona** 项目的判断依据。LLM 做情感/关系分析时必须加载本文件，
> 所有结论都要能**追溯到下面的某个可计算指标**，不允许凭空发挥。
>
> 来源都是公开的关系心理学研究（Gottman 夫妇的实证研究、依恋理论、非暴力沟通等），
> 文末附出处。整理时做了**可计算化改造**——原始理论是给咨询师用的，
> 我们只能看到聊天记录，所以每个概念都要落到"能从消息里算什么"。

---

## 零、必须先读：本方法论的边界

**能做到的**：
- 描述**互动模式**的变化（谁更主动、回复快慢、话题深浅、正负情绪比例）
- 指出**值得注意的信号**，并给出对应指标作为证据
- 提供**沟通方式层面**的建议（怎么说，而不是该不该说）

**不能做到的**（写死在提示词里，禁止模型越界）：
- ❌ 不能诊断人格、心理疾病、依恋障碍
- ❌ 不能预测关系结果（"你们会不会在一起"）
- ❌ 不能替用户做重大决定（表白/分手/结婚）
- ❌ 不能把"没回消息"直接等同于"不在乎"

**核心原则**：
> 给数据，给模式，给几种可能的解释；**把判断权留给用户。**
> 单一指标不能下结论，至少三个指标同向才算"信号"。

---

## 一、Gottman 体系（最可量化的一套）

John & Julie Gottman 对数千对伴侣做了几十年实证研究，是关系领域最扎实的量化体系。
来源：[The Magic Ratio](https://www.gottman.com/blog/the-magic-ratio-the-key-to-relationship-satisfaction/) ·
[Emotional Bank Account](https://www.gottman.com/blog/everyday-ways-to-fill-your-emotional-bank-account/) ·
[Recognizing Bids](https://www.gottman.com/blog/self-care-friendship-and-dr-gottmans-guide-to-recognizing-bids/)

### 1.1 四骑士（Four Horsemen）——关系破坏性沟通

四种高危沟通模式，按破坏力排序：

| 骑士 | 表现 | 在聊天里的可观测特征 |
|---|---|---|
| **批评** Criticism | 攻击人格而非行为 | "你总是…""你从来不…"（对**人**的概括性指责） |
| **蔑视** Contempt | 居高临下、嘲讽、翻白眼 | 讽刺、"呵"、贬低对方能力、模仿嘲笑 |
| **防御** Defensiveness | 反击或推卸 | "是你先…""我又没错""怪我咯" |
| **冷战** Stonewalling | 退出互动 | 已读不回、单字敷衍、话题突然中止 |

**可计算指标**：
```
criticism_rate  = 含"你总是/你从来/你就是"模式的消息数 / 总消息数
contempt_rate   = 含蔑视词表（呵/切/无语/呵呵/服了）的消息数 / 总消息数
defense_rate    = 含反击模式（是你/我又/怪我/我才）的消息数 / 总消息数
stonewall_signal = 对方连续发起 3 次以上未获回应；或回复长度骤降 >70%
```

**对应的解药**（Gottman 给的 antidote，用于建议）：
- 批评 → **温柔开场**（gentle startup）：说具体行为 + 我的感受 + 我需要什么
- 蔑视 → **建立欣赏文化**：每天具体地表达一次欣赏
- 防御 → **承担一部分责任**：哪怕只有 5% 是我的问题，先说那 5%
- 冷战 → **生理自我安抚**：情绪过载时暂停 20 分钟再谈（不是逃避）

### 1.2 魔法比例（The Magic Ratio）

Gottman 的实证结论：稳定关系中**正面互动 : 负面互动 ≈ 5 : 1**（冲突中也要 5:1）。
低于此比例，关系满意度显著下降。

**可计算指标**：
```
positive = 感谢/赞美/关心/玩笑/共情/主动分享 类消息数
negative = 指责/抱怨/嘲讽/冷处理 类消息数
magic_ratio = positive / max(negative, 1)
```
- `>= 5`：健康
- `1~5`：需要留意
- `< 1`：警报，负面互动占主导
- ⚠️ 必须用**较长时间窗口**（≥2 周），单日比值噪音极大

### 1.3 情感账户（Emotional Bank Account）

每次互动都是**存款或取款**。日常小互动（"今天吃了啥"）比偶尔的大礼更重要。

**可计算指标**：
```
daily_deposits = 每日正向互动次数（关心/分享/回应对方分享）
withdrawal_events = 冲突/忽视/贬低事件数
net_balance = 累计存款 - 累计取款
```
**关键洞察**：**平淡的日常闲聊本身就是存款**。不要因为"没什么内容"就忽略它。

### 1.4 情感邀约与回应（Bids & Turning Towards）

Gottman 认为关系的成败藏在**微小邀约**里：对方说"你看这个"，是在发出**连接的邀请**。
三种回应方式：
- **转向**（turning towards）：认真回应 → 关系增强
- **转开**（turning away）：忽略 → 关系削弱
- **对抗**（turning against）：嘲讽/不耐烦 → 关系受损

**可计算指标**（这个最容易算，也最有价值）：
```
bids            = 对方发的"分享类"消息（链接/图片/表情/无明确问题的话题开场）
turned_towards  = 我之后有实质回应的 bids 数（非"嗯""哦"）
turn_rate       = turned_towards / bids
```
> **研究表明 `turn_rate` 是关系质量最灵敏的单一指标之一。**
> 长期低于 30% 是明确的关系风险信号。

---

## 二、依恋类型（Attachment Theory）

来源：[亲密关系中的依恋模式](https://www.163.com/dy/article/ILP939ES05149LPQ.html)

两个维度：**回避程度** × **焦虑程度**，交叉出四种类型。

| 类型 | 回避 | 焦虑 | 核心特征 | 聊天里的表现 |
|---|---|---|---|---|
| **安全型** | 低 | 低 | 能亲近也能独立 | 回复稳定、情绪一致、冲突后能修复 |
| **焦虑型** | 低 | 高 | 怕被抛弃，需要确认 | 追问"你怎么不理我"、回复快且长、对方慢回就焦虑 |
| **回避型** | 高 | 低 | 怕被吞没，重视独立 | 慢回、情绪话题转移、亲密话题变简短 |
| **恐惧型** | 高 | 高 | 想亲近又怕受伤 | 忽冷忽热、推拉交替 |

**可计算指标**（只能给倾向，不能贴标签）：
```
anxiety_signal  = 追问类消息数（"在吗"重复、"你怎么不回"） / 主动发起数
avoidance_signal = 情绪话题中回复长度缩短比例；亲密话题回避率
initiation_ratio = 我主动发起 : 对方主动发起
latency_asymmetry = 我的平均回复间隔 / 对方的平均回复间隔
```
**重要解读规则**：
- **慢回 ≠ 回避型**。工作时段、作息差异都会造成慢回。必须**分时段统计**才有意义。
- 依恋类型不是固定人格，是**关系中的状态**，会随对象和情境变化。

---

## 三、非暴力沟通（NVC）——用于生成建议

Marshall Rosenberg 的四要素框架，把"指责"翻译成"需求表达"：

```
观察（Observation）→ 只说事实，不加评价
感受（Feeling）    → 我的情绪，不是"你让我…"
需要（Need）       → 背后的需求
请求（Request）    → 具体、可执行、可拒绝
```

**示例转换**：
| ❌ 原始 | ✅ NVC 版本 |
|---|---|
| 你总是不回我消息 | 我这两天发的消息你没回（观察），我有点不安（感受），我需要一点确定感（需要），你方便的时候能告诉我你在忙吗（请求） |
| 你怎么这么冷淡 | 感觉最近聊天少了很多（观察），我有点失落（感受），我希望我们能保持联系（需要），周末要不要一起吃饭（请求） |

**生成建议时的硬约束**：
- 用**"我"开头**（I-statement），不用"你总是/你从不"
- 请求必须**可以被拒绝**，否则是要求不是请求
- 一次只提**一件事**，不要翻旧账堆叠

---

## 四、互动质量指标（可直接算，不依赖理论）

这些是最客观的一层，任何分析都要先给这层数据，再谈解读。

### 4.1 主动性

```
initiation_ratio = 我主动开启话题的天数 / 对方主动开启的天数
```
- 长期 **> 2:1** 或 **< 1:2** 都提示失衡
- 健康区间大约 **0.5 ~ 2.0**

### 4.2 回复节奏

```
median_latency_mine   = 我回复对方的中位间隔
median_latency_theirs = 对方回复我的中位间隔
trend = 近 2 周中位间隔 / 前 2 周中位间隔
```
- 单看绝对值没意义，**看趋势**才有意义
- 双方都变慢 → 关系自然降温（不一定是坏事）
- 只有一方变慢 → 值得注意

### 4.3 消息长度与信息密度

```
avg_len_theirs_trend = 对方最近平均消息长度 / 历史平均
deep_topic_ratio     = 涉及情绪/计划/价值观的消息占比
```
- 长度骤降 + 深度话题消失 = 互动表面化
- 注意：**你本来话就短**（中位数 5 字），所以只跟"他自己的历史"比，别跟别人比

### 4.4 互惠性（Reciprocity）

```
self_disclosure_match = 我分享私事的深度 vs 对方分享私事的深度
question_balance      = 我提问数 / 对方提问数
```
互惠是关系推进的核心机制——**单方面自我暴露而不被回应**，是关系停滞的典型原因。

### 4.5 关系阶段（参考五阶段模型）

来源：[关系阶段认知偏差研究](https://enterscholar.com/t/%E5%85%B3%E7%B3%BB%E9%98%B6%E6%AE%B5%E8%AE%A4%E7%9F%A5%E5%81%8F%E5%B7%AE%E7%9A%84%E9%87%8F%E5%8C%96%E8%AF%8A%E6%96%AD%E4%B8%8E%E5%8A%A8%E6%80%81%E5%86%B3%E7%AD%96%E6%A8%A1%E5%9E%8B%E7%A0%94%E7%A9%B6/355512)

该研究指出了一个重要现象：**双方对"关系阶段"的认知常常错位**——
一方以为还在试探期，另一方以为已经确定关系。这在聊天里表现为**投入度不对等**。

```
investment_asymmetry = 我投入（消息数×长度×主动性）/ 对方投入
stage_guess = 根据话题内容（是否涉及未来计划/家人/日常安排）推测阶段
```
输出时**必须**标明这是推测，并提示"建议直接确认，而不是靠猜"。

---

## 五、从指标到建议的推导规则

**这是本方法论的核心执行逻辑。** LLM 必须按此链条推导，不许跳步：

```
第一步：列数据     → 至少 4 个指标的具体数值 + 时间窗口
第二步：找模式     → 哪些指标同向变化？变化从什么时候开始？
第三步：给解释     → 每个模式给 2~3 种**可能解释**（不是一种结论）
第四步：指向方法   → 引用上面的哪个框架（Gottman/NVC/依恋）
第五步：给可执行建议 → 具体到"可以这样说：'…'"，并说明为什么这样说
```

**建议的写法要求**：
- ✅ 具体到**一句话怎么说**（可直接用）
- ✅ 说明**依据**（"因为你的 turns_rate 从 62% 降到 24%"）
- ✅ 给**分级选项**（保守/中性/主动），让用户选
- ❌ 不给"你应该表白/分手"这类决定
- ❌ 不预测结果
- ❌ 不用"他肯定…""说明他…"这类断言

---

## 六、危险信号清单（出现时优先提示用户）

这些不是"判定关系完蛋"，而是**值得本人知道的客观事实**：

| 信号 | 判定条件 | 提示语方向 |
|---|---|---|
| 互动单向化 | `initiation_ratio > 3` 持续 3 周以上 | 提示投入不对等，建议核实而非猜测 |
| 邀约回应率低 | `turn_rate < 30%` 持续 2 周 | 提示可能错失连接机会 |
| 负面比例高 | `magic_ratio < 1` | 提示沟通方式问题（非关系问题） |
| 对话表面化 | 深度话题占比下降 >50% 且长度下降 | 提示关系可能降温 |
| 四骑士出现 | 任一项 `rate > 0.05` | 提示具体沟通模式 + 对应解药 |
| 长期停滞 | 关系阶段 3 个月无推进 | 提示认知可能错位，建议直接沟通 |

---

## 七、出处

- Gottman, J. M. — [The Magic Ratio](https://www.gottman.com/blog/the-magic-ratio-the-key-to-relationship-satisfaction/)、[The Four Horsemen](https://www.leylagulcur.com/blog-leyla-gulcur//four-horsemen-relationships-communication)（Gottman 1994, 1999）
- Gottman, J. M. — [Emotional Bank Account](https://www.gottman.com/blog/everyday-ways-to-fill-your-emotional-bank-account/)、[Recognizing Bids](https://www.gottman.com/blog/self-care-friendship-and-dr-gottmans-guide-to-recognizing-bids/)、[The Mechanics of Bidding](https://www.gottman.com/blog/the-mechanics-of-bidding-messages-you-dont-even-know-youre-sending/)
- 依恋理论 — [亲密关系中，你属于哪种依恋模式](https://www.163.com/dy/article/ILP939ES05149LPQ.html)；依恋的二维空间（avoidance × anxiety）见 [相关论文](https://core.ac.uk/download/544280061.pdf)
- 回避型依恋的具体表现 — [喜欢上回避型恋人，我该怎么办？](https://bjad.com.cn/Html/News/Articles/4793.html)
- 安全型依恋的人际表现 — [台湾清华大学教育学报](https://edujou.site.nthu.edu.tw/var/file/128/1128/img/1172/26-2-2.pdf)
- 非暴力沟通 — Marshall Rosenberg, *Nonviolent Communication*（四要素框架）
- 关系阶段认知偏差 — [五阶段恋爱进展理论的性别视角分析](https://enterscholar.com/t/%E5%85%B3%E7%B3%BB%E9%98%B6%E6%AE%B5%E8%AE%A4%E7%9F%A5%E5%81%8F%E5%B7%AE%E7%9A%84%E9%87%8F%E5%8C%96%E8%AF%8A%E6%96%AD%E4%B8%8E%E5%8A%A8%E6%80%81%E5%86%B3%E7%AD%96%E6%A8%A1%E5%9E%8B%E7%A0%94%E7%A9%B6/355512)
- 修复尝试与对话机制 — [Gottman–Rapoport Conversation](https://www.relationshipinstitute.com.au/post/anatol-rapoport-and-the-gottman-rapoport-conversation)
