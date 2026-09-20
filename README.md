# 微信洞察 · WeChat Insight

把你自己的微信聊天记录变成**看得懂、用得上**的东西，全部在本地跑。

四个功能，一个页面：

| 功能 | 做什么 |
|---|---|
| 🎉 **年度报告** | 结合所有**单聊**的年度总结：15.5 万条、连续聊 107 天、口头禅、称号（深夜话痨/秒回之王）、年度评语 |
| 👤 **聊天与关系** | 选一个人 → 出聊天报告 + **事件驱动的关系分析**（把聊天拆成具体事件：冲突/关心/回避…，点开看原文） |
| 💸 **聊天即消费** | 把聊天按 token 计价：额度、API Key 管理、模型市场、按人塞 Key |
| 🏭 **业务线** | 从群聊/好友里抽结构化信息成表格（家教线索、二手、球局、群聊情报…），支持多条件筛选、语义搜索、处理状态 |

---

## ⚠️ 先读这段：隐私

**这个工具读的是你自己的微信聊天记录，那是最私密的数据之一。**

- ✅ **全部在本机运行**。解密、导入、检索、分析都在你电脑上完成，不上传任何聊天内容。
- ✅ **只读**。不修改微信客户端、不注入、不发送消息。
- ⚠️ **唯一的外部请求**是调用 DeepSeek API 做分析——这一步会把你**选中的那段聊天内容**发给模型。
  不用 AI 功能（年度报告、聊天报告、业务线结果浏览）的话，可以完全离线。
- 🚫 **绝对不要把以下文件提交到 Git 或发给别人**：

  ```
  00-core/chat.db                 你的聊天原文（十几万条）
  all_keys.json                   数据库密钥
  my_texts.jsonl                  你发出的所有消息
  ledger.json / flow_cache.json   账本缓存（含完整聊天）
  draft.json                      你的 API Key
  ```

  仓库里的 `.gitignore` 已经屏蔽了这些。
  **新增功能时如果产生了新文件，先问自己：这是不是私人数据？**

- 📄 导出的 HTML 报告**含联系人名字和聊天原文**，分享前自己过一遍。

---

## 快速开始

### 0. 环境要求

- **Windows**（微信 4.x 的数据库格式与密钥提取都是 Windows 专属）
- **Python 3.10+**
- **微信 4.x** 已登录并产生过消息
- 核心功能零第三方依赖；OCR 相关工具需要 `Pillow`（可选）

```powershell
git clone <这个仓库> wechat-insight
cd wechat-insight
```

### 1. 提取数据库密钥

微信数据库是 SQLCipher 加密的，密钥缓存在进程内存里：

```powershell
# 建议以管理员身份运行 PowerShell（读其他进程内存需要权限）
python tools\wcdb_key_tool_windows.py extract
```

它会自动从 `%APPDATA%\Tencent\xwechat\config\*.ini` 找到你的数据目录，
只读扫描微信进程内存，用 HMAC 校验确认后写入 `all_keys.json`。

> 该工具来自 [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool)（MIT），见致谢。

### 2. 导入聊天记录

```powershell
cd 00-core
python wcstore.py import      # 全量导入，约 30 秒
python wcstore.py stats       # 看统计
python wcstore.py search 充电线   # 直接搜
```

之后微信有新消息，再跑一次 `import` 就是**增量导入**。

### 3. 配置 AI（可选但推荐）

```powershell
# 方式一：环境变量（推荐）
$env:DS_KEY = "sk-你的key"

# 方式二：写文件 — 创建 draft.json
# {"api_key": "sk-你的key"}
```

不配 AI 也能用年度报告、聊天报告、业务线结果浏览；
只有「AI 点评」「关系分析结论」「业务线抽取」需要调模型。

### 4. 启动

```powershell
.\start.ps1
```

会拉起两个服务并打开浏览器：

```
http://127.0.0.1:8772/    主页面（四个功能）
http://127.0.0.1:8899/    聊天即消费（完整版仪表盘，嵌在主页面里）
```

停止：`.\stop.ps1`

若提示"禁止运行脚本"：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

---

## 目录结构

```
00-core/          共享地基
  wcmessages.py     发送者判定（最核心：群聊里哪条是我发的）
  wcstore.py        本地消息库：导入 / 检索 / 上下文
  wcllm.py          LLM 封装（DeepSeek）
  wcweb.py          本地网页服务骨架（路由 / 后台任务 / 内联页面）
  askpopup.py       开发期提问小窗
  wc-design.css     全项目共享设计系统（浅色）
02-report/        报告生成 + 约定挖掘
03-persona/       关系指标计算
04-pipeline/      业务线抽取引擎
05-insight/       ★ 统一页面（四个功能的入口）
  consume/         「聊天即消费」完整版仪表盘（独立服务，iframe 嵌入）
kb/               关系分析方法论 + 需求模拟文档
tools/            数据库解密 / 密钥提取 / OCR
```

---

## 技术上的坑（都是实测踩出来的）

**1. 群聊里 `real_sender_id` 的含义和单聊完全不同**

单聊里它是 `Name2Id` 的 rowid（本人通常是 2）；群聊里它是**群成员序号**，
和 Name2Id 毫无关系。用 `real_sender_id` 判断"群里哪些是我发的"**是错的**
（实测某些群算出 0 条、某些算出 137 条，都不对）。

可靠判据（4 个群交叉验证，100% 成立）：

| 特征 | 含义 |
|---|---|
| 群消息带 `wxid_xxx:` 前缀 | **别人**发的（微信从不为自己的消息加前缀） |
| 不带前缀 + id == 你的 rowid | **你**发的 |

**2. 中文不能用 FTS5 分词**

`unicode61` 对中文不分词（整句当一个 token，"羽毛球"匹配不到），
`trigram` 只支持 3 字以上。15 万条规模用 `LIKE` 子串检索只要 45ms。

**3. 抽取必须从最新往回**

按 `order by local_id`（从最早）+ 数量上限截断，会**永远只处理最早的几块**，
最近几个月一条都进不来。改成 `order by local_id desc`，块内再倒回正序。

**4. LLM 模型选择**

用 `deepseek-chat`——唯一**不产出 reasoning tokens** 的模型，1 秒出干净 JSON。
思考模型做抽取会把 `max_tokens` 吃光导致输出为空。

**5. SQLite 连接不能跨线程**

HTTP 服务是多线程的，`Store` 在主线程创建，一挂到网页就抛 `ProgrammingError`。
修法：`check_same_thread=False` + 调用方加锁。

**6. `.ps1` 文件必须带 UTF-8 BOM**

否则 PowerShell 5.1 按 ANSI 解析中文会乱码，进而语法报错。

**7. 两个前端性能红线**

`background-attachment: fixed` 和 `backdrop-filter` 都不能用，否则滚动掉帧。

---

## 成本

| 操作 | 耗时 | 成本 |
|---|---|---|
| 全量导入 | 30 秒 | 免费 |
| 聊天报告 / 年度报告 | 瞬时 | 免费 |
| 抽一个人的全部事件 | 2~20 分钟 | ¥0.03~0.8 |
| 基于事件生成关系分析 | 20~40 秒 | ¥0.01 |
| AI 点评一段聊天 | 3 秒 | ¥0.0005 |
| 跑一次业务线（5 会话） | 2~5 分钟 | ¥0.2 |

---

## 已知限制

- **只支持 Windows + 微信 4.x**（3.x 数据库格式不同，未适配）
- **不含手机端消息** —— PC 库只有同步下来的部分
- **朋友圈只有本机缓存过的**动态（你刷到过的），不是全量历史
- **关系分析是统计近似**，不能替代真实沟通。它只描述行为模式，
  不做人格诊断、不预测关系结果、不给"该不该表白/分手"这类决定
- **事件抽取会有噪音**，所以每条结论都能点回原文核对，
  样本不足时会明说"看不出模式"

---

## 致谢

- [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool) —— 微信数据库密钥提取（MIT）
- 关系分析方法论参考 Gottman 夫妇的实证研究、依恋理论、非暴力沟通，
  整理在 `kb/relationship_methodology.md`

## 许可

MIT
