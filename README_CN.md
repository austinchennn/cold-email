<p align="right">
  <strong>中文</strong> | <a href="README.md">English</a>
</p>

<p align="center">
  <img src="media/9e4decdd9a339a09d53083e4b1e750e9.jpg" width="200" alt="Cold Email Client Logo"/>
</p>

<h1 align="center">Cold Email Client</h1>

<p align="center">
  AI 驱动的多 Agent 流水线 — 自动发现目标教授、定制简历、发送个性化冷邮件，全流程自动化。
</p>

---

## 项目概览

Cold Email Client 运行一条 5-Agent 顺序流水线：

```
研究方向（用户输入）
        ↓
[Agent 1] 网络搜索 + LLM  →  教授列表
        ↓
[Agent 2] 抓取 + LLM      →  每位教授的深度调研档案
        ↓
[Agent 3] TF-IDF + LLM    →  定制 LaTeX 简历
        ↓
[Agent 4] LLM             →  个性化冷邮件
        ↓
[Agent 5] Gmail API       →  发送 + SQLite 状态追踪
```

内置基于 [Textual](https://github.com/Textualize/textual) 的终端实时仪表盘，可查看各 Agent 进度，并逐条检阅每次 LLM 调用的完整 Prompt 与回复。

---

## 功能特性

- **自动发现教授** — 优先调用 Tavily 搜索 API，自动回退到 DuckDuckGo
- **深度调研** — 抓取实验室主页，通过 LLM 提取研究方向、关键词和技术栈
- **简历定制** — TF-IDF 余弦相似度匹配最相关项目，LLM 改写 bullet points 以契合教授研究
- **个性化邮件** — LLM 基于教授真实研究内容撰写冷邮件
- **Gmail 集成** — OAuth 2.0 发送，含速率限制、指数退避重试和跟进提醒
- **发送追踪** — SQLite 防止重复发送，记录回复状态
- **TUI 仪表盘** — 实时 Agent 状态、步骤进度条、LLM 推理检查器

---

## 技术栈

| 类别 | 库 |
|---|---|
| LLM | `openai`（GPT-4o / Gemini） |
| 网络搜索 | Tavily API、`duckduckgo-search` |
| 网页抓取 | `requests`、`beautifulsoup4` |
| NLP | `scikit-learn`（TF-IDF + 余弦相似度） |
| 邮件发送 | Gmail API（`google-api-python-client`） |
| 重试 | `backoff` |
| 终端 UI | `textual` |
| 数据存储 | SQLite（`sqlite3`） |
| 配置管理 | `python-dotenv` |

---

## 目录结构

```
cold-email/
├── workflow/
│   ├── agents/          # Agent 0–5 流水线逻辑
│   ├── skills/          # 可复用组件（LLM、搜索、Gmail 等）
│   ├── config/          # 配置与领域设置
│   ├── data/            # 教授档案与用户档案
│   ├── latex/           # 简历模板与项目池（.tex）
│   ├── outputs/         # 生成的邮件和定制简历
│   ├── main.py          # 完整流水线（Agent 1–5）
│   ├── run_research.py  # 仅调研（Agent 1–2）
│   ├── run_email.py     # 仅邮件（Agent 3–5）
│   ├── run_intake.py    # 问答采集 + 调研（Agent 0 → 1 → 2）
│   └── dashboard.py     # 实时 TUI 仪表盘
└── tests/               # 单元 + 集成测试（131 个）
```

---

## 安装与配置

### 1. 克隆仓库并安装依赖

```bash
git clone https://github.com/austinchennn/cold-email.git
cd cold-email/workflow
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```env
OPENAI_API_KEY=sk-...          # 必填（或使用 GEMINI_API_KEY）
TAVILY_API_KEY=tvly-...        # 可选，启用更好的搜索
GMAIL_ENABLED=false            # 设为 true 以实际发送邮件
```

### 3. 添加简历项目

将你的 LaTeX 项目块放入 `latex/project_pool/project_01.tex`、`project_02.tex` 等文件，每个文件包含一个 `\resumeProjectHeading{...}` 块。

将简历模板放入 `latex/resume_template/template.tex`，模板中需包含 `%%PROJECTS_BEGIN%%` 和 `%%PROJECTS_END%%` 标记。

### 4.（可选）配置 Gmail 发送

1. 在 Google Cloud Console 创建项目并启用 Gmail API
2. 下载 OAuth 凭证，保存为 `workflow/config/credentials.json`
3. 在 `.env` 中设置 `GMAIL_ENABLED=true`
4. 首次运行时浏览器会弹出 OAuth 授权页面

---

## 使用方式

### 完整流水线（交互式，推荐）

```bash
cd workflow
python run_intake.py
```

通过问答采集你的研究兴趣，然后自动运行完整流水线。

### 仅调研

```bash
python run_research.py
# 或指定领域：
python run_research.py --domain "robot learning"
```

### 仅生成邮件 + 发送（需已有调研数据）

```bash
python run_email.py
# 或处理特定教授：
python run_email.py --slug tim_barfoot
```

### 实时仪表盘

```bash
python dashboard.py
```

---

## 环境变量说明

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI API 密钥（与 Gemini 二选一） |
| `GEMINI_API_KEY` | — | Google Gemini API 密钥（优先级高于 OpenAI） |
| `LLM_MODEL` | `gpt-4o` | 使用的模型名称 |
| `LLM_TEMPERATURE` | `0.3` | 生成温度 |
| `TAVILY_API_KEY` | — | Tavily 搜索 API 密钥（可选） |
| `MAX_PROFESSORS` | `10` | 每次最多发现的教授数量 |
| `TOP_K_PROJECTS` | `3` | 注入简历的项目数量 |
| `GMAIL_ENABLED` | `false` | 是否启用实际 Gmail 发送 |
| `GMAIL_DAILY_LIMIT` | `50` | 每日最大发送数量 |
| `GMAIL_DELAY_MIN` | `60` | 两封邮件之间的最短间隔（秒） |
| `GMAIL_DELAY_MAX` | `120` | 两封邮件之间的最长间隔（秒） |
| `GMAIL_FOLLOW_UP_DAYS` | `3` | 跟进邮件的等待天数 |

---

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) — 个人及学术研究使用免费；**禁止商业用途**。
