# EventBus 架构技术文档

> 文档版本：v1.0 · 适用代码：`workflow/skills/event_bus.py`

---

## 目录

1. [架构概述](#1-架构概述)
2. [为什么必须有 EventBus](#2-为什么必须有-eventbus)
3. [它是传统架构吗](#3-它是传统架构吗)
4. [本项目的创新点](#4-本项目的创新点)
5. [核心数据结构](#5-核心数据结构)
6. [代码走读](#6-代码走读)
7. [使用示例](#7-使用示例)
8. [设计权衡](#8-设计权衡)

---

## 1. 架构概述

EventBus（事件总线）是本项目的**进程内消息中枢**。它连接两类角色：

| 角色 | 代码位置 | 职责 |
|------|----------|------|
| **Publisher（发布者）** | `agents/agent1_search.py` … `agent5_send.py` | 执行任务，随时向总线 `post()` 进度事件 |
| **Subscriber（订阅者）** | `dashboard.py`、测试框架 | `subscribe()` 拿到一个专属队列，轮询读取事件并渲染 |

整体拓扑如下（逻辑流向，非时序）：

```
┌──────────────────────────────────────────────────────────┐
│                    Workflow Thread                        │
│                                                          │
│  Agent1 ──bus.post()──┐                                  │
│  Agent2 ──bus.post()──┤                                  │
│  Agent3 ──bus.post()──┼──► EventBus ──fan-out──► Queue1 ─► Dashboard
│  Agent4 ──bus.post()──┤             (n个订阅者)  Queue2 ─► Test Harness
│  Agent5 ──bus.post()──┘                         QueueN ─► ... 
│                                                          │
└──────────────────────────────────────────────────────────┘
```

设计原则：

- **单向数据流**：只有 `post → fan-out → consume`，没有回调、没有双向耦合。
- **Zero-coupling**：发布者不知道订阅者的存在；订阅者不知道发布者的业务逻辑。
- **Non-blocking**：发布操作绝不阻塞工作流线程。

---

## 2. 为什么必须有 EventBus

### 2.1 问题背景：长流程 + 实时可视化的矛盾

本项目的主工作流（`main.py`）是一个**串行、阻塞**的五阶段 AI Pipeline：

```
用户输入 → Agent1(搜索) → Agent2(调研) → Agent3(简历) → Agent4(邮件) → Agent5(发送)
```

每个 Agent 内部有多次 LLM 调用、网络抓取、LaTeX 编译……**单个教授可能需要 60–120 秒**。

同时，`dashboard.py` 需要在 TUI 界面**实时**展示每个 Agent 的进度、LLM 调用内容、错误信息。

这产生了一个经典的**并发通信难题**：

| 选项 | 问题 |
|------|------|
| 直接调用 Dashboard 的 UI 方法 | 产生双向耦合；UI 层渗透业务层 |
| 共享全局变量 | 需要粗粒度锁；竞态条件难以排查 |
| 多线程 + 回调 | 回调地狱；测试困难；异常难传播 |
| 轮询文件/数据库 | IO 开销大；实时性差；架构复杂 |
| **EventBus（本方案）** | 解耦；线程安全；可测试；零 IO |

### 2.2 EventBus 解决的核心问题

1. **解耦生产与消费**：Agent 只调用 `bus.post()`，完全不关心 Dashboard 是否存在、是否正在运行。
2. **不阻塞工作流**：即使 Dashboard 渲染缓慢或崩溃，`bus.post()` 会静默丢弃满队列的事件，工作流继续执行。
3. **历史回放**：Dashboard 启动晚于工作流时，可通过 `bus.history()` 读取已发生的所有事件，重建完整状态。
4. **可测试性**：测试代码只需 `bus.subscribe()` + `bus.reset()`，不需要 Mock 任何 UI 组件。

---

## 3. 它是传统架构吗

### 3.1 传统的 Pub/Sub 模式

EventBus 的根是 **发布-订阅模式（Publish-Subscribe）**，最早由 Birman & Joseph 在 1987 年的分布式系统论文中正式描述，后被 GoF（Gang of Four）的《设计模式》以"观察者模式（Observer）"的变体收录。

主流框架中的对应实现：

| 框架/平台 | 实现名称 |
|-----------|----------|
| Java Spring | `ApplicationEventPublisher` |
| Android | `EventBus`（GreenRobot） |
| JavaScript | `EventEmitter`（Node.js）、`RxJS Subject` |
| 分布式系统 | Kafka、RabbitMQ、Redis Pub/Sub |

所以从**模式层面**看，EventBus 是一个有 40 年历史的成熟架构思想。

### 3.2 本项目的定位

本项目的 EventBus 是一个**进程内（in-process）、线程间的轻量级实现**，不走网络、不做序列化，与 Kafka 这类分布式消息队列处于完全不同的层次。

最接近的传统对标是 Java 的 **Guava EventBus**（2012），但两者在线程模型和背压策略上有显著差异（见第 4 节）。

---

## 4. 本项目的创新点

### 4.1 创新一：`__new__` 单例 + 初始化幂等保护

**传统做法**（Java/Python 常见）：

```python
# 传统：用类变量 + 双重检查锁
_instance = None
_lock = threading.Lock()

def get_instance():
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = EventBus()
    return _instance
```

**本项目做法**：

```python
class EventBus:
    _instance: Optional["EventBus"] = None
    _class_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "EventBus":
        with cls._class_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._lock        = threading.Lock()
                inst._history     = []
                inst._subscribers = []
                cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        pass  # 故意为空：所有初始化在 __new__ 完成
```

**创新之处**：

- `__init__` 故意留空：防止"每次 `EventBus()` 都重置状态"的 Python 陷阱（`__init__` 在 `__new__` 之后必然被调用）。
- 所有内部状态在 `__new__` 中一次性初始化，即使多个模块同时执行 `EventBus()`，状态只初始化一次。
- 锁本身（`_class_lock`）是类变量，比实例锁更早存在，保护 `__new__` 的临界区。

### 4.2 创新二：非阻塞发布 + 有界队列背压

**传统 Observer 模式的问题**：观察者的 `update()` 在发布者线程中同步执行，观察者越慢发布者越慢。

**本项目的解法**：

```python
def post(self, event: Event) -> None:
    with self._lock:
        self._history.append(event)
        for q in self._subscribers:
            try:
                q.put_nowait(event)   # ← 非阻塞！
            except _stdlib_queue.Full:
                pass                  # ← 丢弃而非阻塞
```

每个订阅者有独立的有界队列（`maxsize=4000`）：

```python
def subscribe(self) -> _stdlib_queue.Queue:
    q: _stdlib_queue.Queue = _stdlib_queue.Queue(maxsize=4000)
    ...
```

**效果**：

- 工作流线程（AI Pipeline）永远不会因为 Dashboard 渲染慢而被阻塞。
- 若事件积压超过 4000 条（极端情况），新事件被静默丢弃——可视化降级，但核心功能不受损。
- 4000 的上界防止订阅者内存无限增长（尤其在测试场景中）。

### 4.3 创新三：History Tape（事件历史带）

传统 Pub/Sub 是**无记忆**的——订阅者在 `subscribe()` 之前发生的消息永远丢失。

本项目维护了一条**顺序不可变的历史链表**：

```python
self._history: List[Event] = []

def history(self, agent_id: Optional[int] = None) -> List[Event]:
    with self._lock:
        if agent_id is None:
            return list(self._history)   # 返回副本，不暴露内部引用
        return [e for e in self._history if e.agent_id == agent_id]
```

**使用场景**：

```python
# Dashboard 启动时，重放所有已发生的事件来还原初始 UI 状态
for event in bus.history():
    self._apply_event(event)

# 然后再订阅新事件
q = bus.subscribe()
```

这让 Dashboard 的启动时机不再关键，随时可以接入并获得完整的上下文。

### 4.4 创新四：`str` Enum 的事件类型

```python
class EventType(str, Enum):
    AGENT_START    = "agent_start"
    AGENT_STEP     = "agent_step"
    AGENT_COMPLETE = "agent_complete"
    LLM_CALL       = "llm_call"
    LLM_RESPONSE   = "llm_response"
    ...
```

继承 `str` 而非普通 `Enum` 的好处：

- 事件类型**直接可序列化**为 JSON，无需额外转换（`json.dumps({"type": EventType.AGENT_START})` 输出 `"agent_start"`）。
- 在日志、Dashboard 渲染中无需 `.value` 访问，直接 `str(event.type)` 即可。
- 保留 Enum 的**类型安全**：IDE 可以自动补全，typo 在运行前即可发现。

---

## 5. 核心数据结构

### 5.1 `Event`（数据类）

```python
@dataclass
class Event:
    type:     EventType
    agent_id: int                          # 1-5；0 = 工作流级别
    data:     Dict[str, Any] = field(default_factory=dict)
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `EventType` | 事件种类（枚举） |
| `agent_id` | `int` | 哪个 Agent 发出（0 为工作流级全局事件） |
| `data` | `dict` | 自由字典，携带阶段相关的 payload |

使用 `@dataclass` 而非普通类的好处：自动生成 `__repr__`、`__eq__`，`default_factory=dict` 避免可变默认参数陷阱。

### 5.2 `queue.Queue`（有界 FIFO 队列）

Python 标准库 `queue.Queue` 是**线程安全的阻塞队列**，基于 `collections.deque` + `threading.Condition` 实现。

本项目将其用作**每个订阅者的专属信箱**，关键参数：

```python
_stdlib_queue.Queue(maxsize=4000)
```

- `put_nowait(item)`：非阻塞入队，满则抛 `Full`（发布者捕获后静默丢弃）。
- `get(timeout=0.1)`：订阅者侧的带超时出队，超时则继续轮询，不死锁。

### 5.3 `_history: List[Event]`（顺序历史链表）

Python `list` 的 `append()` 均摊 O(1)，读取时复制整列表（快照语义，防止外部修改内部状态）。

每次 `post()` 都向此列表追加，因此历史有序且完整。`bus.reset()` 时清空，供测试场景复用。

### 5.4 `_subscribers: List[queue.Queue]`（订阅者注册表）

普通 Python 列表，存储所有活跃订阅者的队列引用。

- `subscribe()` 追加新队列。
- `unsubscribe(q)` 调用 `list.remove(q)`，通过**对象身份**（`is`）定位并移除。
- 所有对此列表的读写均在 `_lock` 保护下进行。

### 5.5 `threading.Lock`（互斥锁）

| 锁 | 作用域 | 保护对象 |
|----|--------|----------|
| `_class_lock`（类变量） | `EventBus.__new__` | 单例创建过程 |
| `_lock`（实例变量） | `post / subscribe / unsubscribe / history / reset` | `_history`、`_subscribers` |

两把锁的粒度不同，避免了单一全局锁的竞争热点。

---

## 6. 代码走读

```
event_bus.py
│
├── EventType(str, Enum)          # 所有合法事件类型，12 个
├── Event(@dataclass)             # 轻量事件对象：type + agent_id + data
└── EventBus
    ├── __new__()                 # 单例构造 + 内部状态初始化
    ├── __init__()                # 故意为空（幂等保护）
    │
    ├── post(event)               # Publisher API：写 history，fan-out 到各 Queue
    │
    ├── subscribe()               # Subscriber API：返回新 Queue
    ├── unsubscribe(q)            # 取消订阅，移除 Queue
    │
    ├── history(agent_id=None)    # 读取历史快照（可按 Agent 过滤）
    └── reset()                   # 清空所有状态（供测试使用）

bus = EventBus()                  # 模块级单例，全项目 import 这一个对象
```

### 生命周期时序（Dashboard 场景）

```
T=0  main.py 启动
       └─ bus = EventBus()（首次实例化）

T=1  dashboard.py 在新线程启动
       └─ for ev in bus.history(): 回放历史
       └─ q = bus.subscribe()

T=2  Agent1 执行
       └─ bus.post(Event(AGENT_START, 1, {...}))
            ├─ 追加到 _history
            └─ q.put_nowait(ev)  →  Dashboard 轮询到，更新 UI

T=3  Agent2 … Agent5 同理

T=N  Dashboard 关闭
       └─ bus.unsubscribe(q)
```

---

## 7. 使用示例

### 发布事件（Agent 侧）

```python
from skills.event_bus import bus, Event, EventType

# Agent 启动
bus.post(Event(EventType.AGENT_START, agent_id=2, data={"professor": "Yann LeCun"}))

# 某个步骤完成
bus.post(Event(EventType.AGENT_STEP, agent_id=2, data={"step": "Scrape lab page"}))

# LLM 调用
bus.post(Event(EventType.LLM_CALL, agent_id=2, data={
    "label": "extract_keywords",
    "system": "You are ...",
    "user": "Extract keywords from ...",
}))

# Agent 完成
bus.post(Event(EventType.AGENT_COMPLETE, agent_id=2, data={"path": "data/...json"}))
```

### 订阅事件（Dashboard / 测试侧）

```python
from skills.event_bus import bus

q = bus.subscribe()

try:
    while True:
        try:
            event = q.get(timeout=0.1)   # 100ms 超时，保持响应性
            print(f"[Agent {event.agent_id}] {event.type}: {event.data}")
        except queue.Empty:
            pass  # 没有新事件，继续轮询
finally:
    bus.unsubscribe(q)
```

### 历史回放

```python
# 获取所有 Agent2 的历史事件
for event in bus.history(agent_id=2):
    print(event)

# 获取全部历史
all_events = bus.history()
```

---

## 8. 设计权衡

| 决策 | 选择 | 放弃的选项 | 理由 |
|------|------|-----------|------|
| 并发模型 | 多线程（`threading`） | `asyncio` | 工作流使用同步 LLM 客户端；`asyncio` 会要求全栈改造 |
| 背压处理 | 静默丢弃（`put_nowait` + 捕获 Full） | 阻塞等待 / 背压通知 | 工作流线程优先级高于 UI；宁可丢弃进度条，不可阻塞发邮件 |
| 历史存储 | 进程内 `List[Event]` | 持久化到 SQLite | 本项目仅需进程内回放；持久化由 `send_tracker.py` 负责 |
| 事件 payload | 自由 `Dict[str, Any]` | 强类型 Protocol / Pydantic | 快速迭代；各 Agent 的 data 字段差异大，强类型反而冗余 |
| 单例模式 | 模块级 `bus` 对象 | 依赖注入 | 简化 Agent 代码；Python 模块天然是单例，`import bus` 即可 |

---

*本文档由项目作者生成，与 `workflow/skills/event_bus.py` 代码保持同步。*
