# SQLite 在本项目中的使用

> 这篇文档记录 SQLite 在 Cold Email Client 中解决的问题、业务逻辑设计、以及具体的代码实现。

---

## 为什么用 SQLite

### 核心需求是什么

Agent 5 每次运行都会向教授发送邮件。这里有一个根本问题：**同样一封邮件绝对不能发两次**。用户可能：

- 手动重跑整个 pipeline
- 运行 `run_email.py --slug xxx` 重新处理某个教授
- 程序中途崩溃后重新启动

如果没有持久化的发送记录，每次重跑都会重新发邮件，这在学术冷邮件场景里是严重失误——教授收到同一封邮件两三次，不仅不会回复，还会让人显得极不专业。

内存里的 set 或 dict 解决不了这个问题，程序一退出就消失了。必须有一个持久化存储。

### 为什么不用文件（JSON / CSV）

最简单的替代方案是把发送记录写成 JSON 文件。但这个方案有几个问题：

1. **并发写入不安全**。如果未来扩展成多进程发送，两个进程同时写同一个 JSON 文件会导致数据损坏。SQLite 有内置的文件锁，天然处理这个问题。
2. **查询效率**。随着发送记录增多，"这个教授有没有发送过"这个查询用 JSON 需要遍历整个文件；SQLite 用索引，O(log n)。
3. **事务保证**。SQLite 的每次 INSERT/UPDATE 是原子的，要么成功要么完全回滚，不会出现写到一半的半完整记录。

### 为什么不用 PostgreSQL / MySQL

这个工具是单机运行的个人工具，没有网络服务、没有多用户并发。用 PostgreSQL 意味着：
- 用户需要额外安装并启动一个数据库服务
- 需要配置连接字符串、用户名密码
- 部署门槛大幅提升

SQLite 是一个文件，零配置，随项目目录走。对于"个人冷邮件工具"这个使用规模，SQLite 完全够用，引入重型数据库反而是过度设计。

---

## 业务逻辑

### 数据库的职责

SendTracker 扮演的角色是**发送台账**，记录每一封邮件从发送到收到回复的完整生命周期：

```
发送前检查 → 发送 → 记录结果 → 定期检查跟进 → 标记已回复
```

具体状态流转：

```
初次发送成功 → status = 'sent'    follow_up_at = 3天后
收到回复     → status = 'replied' follow_up_at = NULL（不再跟进）
发送失败     → status = 'failed'  gmail_message_id = NULL
跟进已发出   → follow_up_sent = 1
```

### 防重复发送

这是 SQLite 在本项目中最关键的业务用途。Agent 5 每次运行时，第一件事是查询数据库：

```
if tracker.has_been_sent(slug):
    跳过这位教授，继续下一位
```

`slug` 是教授的唯一标识符（由姓名生成，如 `tim_barfoot`）。只要数据库里有这位教授 `status='sent'` 的记录，无论重跑多少次都不会重复发送，这是整个邮件安全机制的核心。

### 跟进邮件调度

发送成功后，系统会计算一个跟进时间：`发送时刻 + N天`，存入 `follow_up_at` 字段（ISO-8601 UTC 时间字符串）。

外部程序（或定时任务）可以调用 `get_due_followups()` 查询所有到期未跟进的记录，据此决定是否发送跟进邮件。这个机制保证了：
- 如果教授没有回复，N 天后自动提示可以跟进
- 如果教授已回复（调用了 `mark_replied()`），`follow_up_at` 被清空，不再出现在跟进查询结果里

### 失败记录

即使发送失败，也会写入数据库（`status='failed'`）。这样做的原因是：
- 保留发送尝试的历史，方便排查
- `has_been_sent()` 只查 `status='sent'`，失败记录不会阻止重试

---

## 数据库结构

```sql
CREATE TABLE sent_emails (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    slug             TEXT    NOT NULL,        -- 教授唯一标识
    professor_name   TEXT    NOT NULL,
    to_email         TEXT    NOT NULL,
    subject          TEXT    NOT NULL,
    sent_at          TEXT    NOT NULL,        -- ISO-8601 UTC 时间戳
    gmail_message_id TEXT,                   -- Gmail API 返回的消息ID，失败时为 NULL
    status           TEXT    NOT NULL DEFAULT 'sent',  -- sent | replied | bounced | failed
    follow_up_at     TEXT,                   -- 跟进截止时间，NULL 表示无需跟进
    follow_up_sent   INTEGER NOT NULL DEFAULT 0        -- 0=未发, 1=已发
);

CREATE INDEX idx_slug ON sent_emails(slug);
CREATE INDEX idx_gid  ON sent_emails(gmail_message_id);
```

两个索引的选择：
- `idx_slug`：`has_been_sent()` 每次发送前都要查，是高频路径
- `idx_gid`：`mark_replied()` 通过 Gmail message ID 定位记录，Gmail webhook 回调时使用

---

## 代码实现

### 入口：`skills/send_tracker.py`

整个 SQLite 操作封装在 `SendTracker` 类中，其他模块不直接接触 `sqlite3`。

**连接管理** — 每次操作新建连接，用完即关：

```python
def _conn(self) -> sqlite3.Connection:
    return sqlite3.connect(str(self._db_path))
```

没有用连接池，原因是：SQLite 在 WAL（Write-Ahead Logging）模式下支持多连接并发读，而这个工具是顺序执行的，连接池带来的性能提升微乎其微，反而增加了连接泄漏的风险。每次用完自动归还（`with self._conn() as conn:` 利用上下文管理器自动 commit + close）。

**参数化查询** — 所有 SQL 都用 `?` 占位符：

```python
conn.execute("SELECT 1 FROM sent_emails WHERE slug=? AND status='sent' LIMIT 1", (slug,))
```

不拼接字符串，防止 SQL 注入（虽然这是本地工具，但习惯要养好）。

### 各方法用途

| 方法 | 调用时机 | SQL 操作 |
|---|---|---|
| `_init_db()` | 构造函数，首次运行自动建表 | `CREATE TABLE IF NOT EXISTS` |
| `has_been_sent(slug)` | Agent 5 每次发送前，防重复 | `SELECT 1 ... LIMIT 1` |
| `record_sent(...)` | Gmail API 返回 message ID 后 | `INSERT` |
| `record_failure(...)` | 发送异常或 Gmail 返回 None | `INSERT` with `status='failed'` |
| `mark_replied(gmail_id)` | 检测到教授回复后 | `UPDATE status='replied', follow_up_at=NULL` |
| `get_due_followups()` | 定时检查，找到期跟进 | `SELECT WHERE status='sent' AND follow_up_at<=NOW` |
| `mark_followup_sent(gmail_id)` | 跟进邮件发出后 | `UPDATE follow_up_sent=1` |
| `stats()` | 统计各状态数量（dashboard 展示） | `SELECT status, COUNT(*) GROUP BY status` |

### 调用链：Agent 5 中的使用

```python
# agent5_send.py

tracker = self._get_tracker()          # 懒加载，首次调用才实例化

# 1. 发送前检查
if tracker.has_been_sent(slug):
    return None                        # 直接跳过，不发送

# 2. 调用 Gmail API 发送
gmail_id = mailer.send_email(...)

# 3. 写入结果
if gmail_id:
    tracker.record_sent(
        slug, name, email, subject,
        gmail_message_id=gmail_id,
        follow_up_days=3              # 3天后可跟进
    )
else:
    tracker.record_failure(slug, name, email, subject, "send returned None")

# 4. 异常时也要记录失败，避免静默重试
except Exception as exc:
    self._get_tracker().record_failure(slug, name, email, "", str(exc))
```

懒加载（`_get_tracker()`）的意图是：`GMAIL_ENABLED=false` 时 Agent 5 处于 dry-run 模式，可以完全不接触数据库，避免创建无意义的 `.db` 文件。

---

## 数据文件位置

```
data/send_status.db
```

此文件在 `.gitignore` 中被排除，不会提交到仓库——每个用户的发送记录是私有的，也不应该被版本控制追踪。
