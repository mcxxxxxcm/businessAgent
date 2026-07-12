# 对抗性审查：3个月后系统最可能出问题的地方

> 审查方法：对抗性思维 + 第一性原理
> 核心逻辑：系统的失效模式取决于它对哪些假设做了"不变量"假设，而这些假设随时间会打破。

---

## 🔴 第一梯队：必然发生，只是时间问题

### 1. LangGraph Checkpoint 数据库膨胀 → 磁盘满 / 查询崩塌 ✅ 已修复

**这是最确定的定时炸弹。**

```
不变量假设：存储空间无限
现实：每个对话轮次产生2-3个checkpoint，日积月累
```

推算：
- 每对话 ~10 轮 × ~3 checkpoint/轮 = 30 条/对话
- 日 1000 对话 → 30,000 条/天
- **3个月 → ~270万条**
- `checkpoints` + `checkpoint_blobs` + `checkpoint_writes` 三张表连带 BLOB 数据

后果路径：磁盘空间不足 → PG 写入变慢 → 连接池满 → **所有请求 500**，不是降级，是彻底不可用。

而且，LangGraph 的 `AsyncPostgresSaver` **每次 ainvoke 都会写 checkpoint**，即使用户只是发了一句"你好"。

**涉及文件**：
- `app/core/postgres.py` — 无清理机制
- `app/memory/checkpointer.py` — 无 TTL 配置

**修复建议**：添加定时清理任务，清理超过 N 天的 checkpoint 记录。可使用 LangGraph 的 `checkpointer` API 或直接 SQL 清理。

---

### 2. SMS SDK 同步调用阻塞事件循环 → 全局卡顿 ✅ 已修复

```python
# sms.py — 在 async 函数中直接调用同步 SDK
async def _send_sms_aliyun(...):
    client = Client(config)       # 同步
    response = client.send_sms()  # 同步，阻塞！
```

**不变量假设**：SMS SDK 是异步的。**现实**：阿里云/腾讯云 Python SDK 都是同步的。

后果路径：一条短信发送耗时 1-3 秒 → 期间整个 asyncio 事件循环被阻塞 → **所有用户的所有请求都卡住**。不是这一个用户慢，是所有人都卡。

3个月后系统流量上升，某个退款场景触发批量短信 → 雪崩。

**涉及文件**：`app/tools/sms.py`

**修复建议**：用 `await asyncio.to_thread(client.send_sms, request)` 包裹同步调用，或使用异步 HTTP 客户端（如 httpx）直接调用短信 API。

---

### 3. 全局单例初始化竞态 → 连接池泄漏 ✅ 已修复

```python
# postgres.py, redis.py, deps.py — 同一个模式
_pool = None
async def get_pg_pool():
    global _pool
    if _pool is None:              # ← 协程A检查通过
        _pool = AsyncConnectionPool(...)  
        await _pool.open()         # ← 协程A在这里挂起
                                  # ← 协程B进入，_pool 已非None但未open完成
```

**不变量假设**：初始化只会被调用一次。**现实**：FastAPI 的 lifespan + 首次请求并发 = 竞态。

后果路径：创建两个连接池 → 第一个被覆盖但未关闭 → 10个PG连接永久泄漏 → 达到 PG `max_connections` → **新连接被拒绝**。

3个月后某次重启时恰好有并发请求进来，竞态触发，但运维看到的是"PG连接数异常"却找不到原因。

**涉及文件**：
- `app/core/postgres.py`
- `app/core/redis.py`
- `app/api/deps.py`

**修复建议**：使用 `asyncio.Lock` 保护初始化：
```python
_init_lock = asyncio.Lock()

async def get_pg_pool():
    global _pool
    if _pool is not None:
        return _pool
    async with _init_lock:
        if _pool is None:  # double-check
            _pool = AsyncConnectionPool(...)
            await _pool.open()
    return _pool
```

---

## 🟡 第二梯队：高概率发生，但不会立即致命

### 4. Redis 宕机 → 限流失效 + 会话丢失 → 静默降级到裸奔 ✅ 已修复

```python
# middleware.py
except Exception:
    pass  # ← 完全静默

# cache.py 所有方法
except Exception as e:
    logger.warning("Redis...失败: %s", e)  # ← warning级别，不会告警
```

**不变量假设**：Redis 始终可用或故障会有人发现。**现实**：Redis 宕机后系统无声降级——无限流、无缓存、无反馈计数、无在线状态。

后果路径：Redis 8小时宕机 → 无限流被恶意请求打满 → LLM API 费用暴涨 → 人工客服收不到转人工通知 → 用户投诉增多。

3个月后 Redis 做了一次主从切换，5分钟不可用，但没人知道系统在裸奔。

**涉及文件**：
- `app/api/middleware.py`
- `app/memory/cache.py`

**修复建议**：
1. 将 `pass` 改为 `logger.error` + 递增告警指标（如 Prometheus counter）
2. 添加 Redis 健康检查到 `/health` 端点
3. Redis 不可用时返回 503 而非静默放行

---

### 5. 限流 `incr + expire` 非原子 → IP 被永久限流 ✅ 已修复

```python
current = await r.incr(key)
if current == 1:
    await r.expire(key, 60)  # ← 如果这里crash了？key永远存在
```

**不变量假设**：`incr` 和 `expire` 之间不会crash。**现实**：OOM kill、部署滚动更新、网络抖动都可能中断。

后果路径：某次部署时恰好有请求在 `incr` 和 `expire` 之间 → key 永不过期 → 该 IP 永远被限流 → 用户投诉"一直403" → 运维查不到原因（限流key没有明显标记）。

**涉及文件**：`app/api/middleware.py`

**修复建议**：使用 Lua 脚本保证原子性：
```python
LUA_RATE_LIMIT = """
local current = redis.call('incr', KEYS[1])
if current == 1 then
    redis.call('expire', KEYS[1], ARGV[1])
end
return current
"""
```

---

### 6. CORS `allow_origins=["*"]` + `allow_credentials=True` → CSRF ✅ 已修复

```python
allow_origins=["*"],
allow_credentials=True,
```

这不是"3个月后"的问题，是**上线第一天就有的漏洞**。但3个月后可能被利用——恶意网站可以冒充用户发送聊天请求、提交反馈、触发转人工等。

根据 CORS 规范，`allow_origins=["*"]` 与 `allow_credentials=True` 不能同时使用。Starlette 会将 `*` 替换为请求的 Origin，实际效果等同于允许任何域携带凭证访问 API——**CSRF 攻击风险**。

**涉及文件**：`app/api/middleware.py`

**修复建议**：
1. 将 `allow_origins` 改为具体域名列表（从配置读取）
2. 如果需要支持多域名，移除 `allow_credentials=True` 或实现动态 Origin 校验

---

## 🟠 第三梯队：缓慢恶化，不易察觉

### 7. 用户画像 read-modify-write 竞态 → 数据丢失 ✅ 已修复

```python
profile = await load_user_profile(...)   # 读
profile.total_conversations += 1          # 改
await save_user_profile(...)              # 写
```

同用户两个会话并发 → 各自读到 count=5 → 各自写回 count=6 → **丢失一次计数**。

3个月后：用户画像中的交互次数、满意度评分、转人工次数**越来越不准**，但没人发现，直到运营做数据分析时发现数据对不上。

**涉及文件**：
- `app/memory/profile.py` — `update_profile_from_state()`
- `app/memory/manager.py` — `save_memory_after_response()`

**修复建议**：使用 PG 的原子操作（如 `UPDATE SET count = count + 1`）替代 read-modify-write 模式，或使用乐观锁（版本号）检测并发冲突。

---

### 8. 结构化输出四层降级 → 最坏情况超2分钟 ✅ 已修复

**不变量假设**：降级是快速失败。**现实**：每层都是完整LLM调用。

Layer1 超时 → Layer2 超时 → Layer3 超时 → Layer4 超时。如果API响应慢（10s/次），总计 40 秒。如果 API 返回 5xx 但不是超时，可能无限重试。

3个月后某天智谱API做了一次变更，`with_structured_output` 返回的格式变了 → Layer1 总是失败 → 每次请求多花 10s → 用户感知变慢 → 流量下降。

**涉及文件**：`app/agent/schemas.py`

**修复建议**：
1. 为每层设置 `request_timeout`（如 5s），超时即降级
2. Layer1 和 Layer2 对智谱 API 可能等价（默认 method 就是 tool_calling），应跳过等价层
3. 添加降级指标统计，监控每层命中率和耗时

---

### 9. Token 估算偏差 → 上下文溢出 ✅ 已修复

```python
len(content) // 2  # 中文1字≈1-2token，这里严重低估
```

3个月后对话越来越长，估算偏差累积 → 摘要触发太晚 → **LLM context 超出** → 请求失败 → 用户看到"抱歉，生成回复时遇到了问题"。

**涉及文件**：`app/memory/summarizer.py`

**修复建议**：使用 `tiktoken` 或 LangChain 的 `get_num_tokens()` 替代粗略估算。至少对中文使用 `len(content) * 1.5` 的更保守估算。

---

## 📊 风险矩阵总结

| 问题 | 必然性 | 影响范围 | 发现难度 | 3月后概率 |
|------|--------|---------|---------|----------|
| Checkpoint 膨胀 | ⭐⭐⭐ | 全局不可用 | 低（磁盘告警） | **极高** |
| SMS 阻塞事件循环 | ⭐⭐⭐ | 全局卡顿 | 中（间歇性） | **高** |
| 单例竞态泄漏 | ⭐⭐ | 连接耗尽 | 高（难以复现） | 中 |
| Redis 静默降级 | ⭐⭐ | 裸奔 | 极高（无告警） | **高** |
| 限流原子性 | ⭐ | 个别IP被封 | 高 | 中 |
| CORS 漏洞 | ⭐ | CSRF攻击 | 低 | 取决于曝光度 |
| 画像竞态 | ⭐ | 数据不准 | 极高 | **极高**（持续发生） |
| 四层降级延迟 | ⭐ | 体验变差 | 中 | 中 |
| Token 估算偏差 | ⭐ | 偶发context溢出 | 高 | 中 |

---

## 🎯 如果只能修3个，修哪个？

### 1. Checkpoint 定期清理

加个定时任务，清理超过 N 天的 checkpoint。这是最确定会爆的问题，且修复成本最低。

### 2. SMS SDK 改 `run_in_executor`

一行代码包一下 `await asyncio.to_thread(client.send_sms, request)`，防止阻塞事件循环。

### 3. Redis 故障告警

把所有 `pass` 和 `logger.warning` 改成结构化告警指标（如 Prometheus counter），让运维能"看到"降级正在发生。

---

## 附录：逐文件审查详情

### `app/core/postgres.py` — PG连接池

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| 竞态条件 | 高 | 全局单例 `_pool` 初始化无异步锁保护，并发调用可能创建多个连接池 |
| 连接池关闭无超时 | 中 | `await _pool.close()` 无 timeout，PG不可达时可能无限挂起 |
| 连接池参数硬编码 | 中 | `min_size=2, max_size=10` 硬编码，无法通过环境变量调整 |
| `prepare_threshold=0` | 低 | 禁用预备语句缓存，轻微性能损失 |
| close未检查open状态 | 低 | `close_pg_pool` 不检查池是否已成功 open |

### `app/core/redis.py` — Redis连接

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| 竞态条件 | 高 | 与 PG 池相同的无锁单例初始化模式 |
| `max_connections=50` 硬编码 | 中 | 无法通过配置调整，高并发可能不够 |
| 无连接超时 | 中 | 未设置 `socket_connect_timeout`、`socket_timeout`，Redis慢时无限等待 |
| 无重试策略 | 低 | Redis操作中途断连时抛异常，需调用方自行处理 |
| close不等待进行中操作 | 低 | 有进行中 Redis 操作时调用 `close_redis()` 可能中断命令 |

### `app/api/deps.py` — 依赖注入

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| `llm_semaphore = asyncio.Semaphore(5)` 硬编码 | 中 | 无法根据 API 速率限制调整，多进程下总并发 = 5×N |
| 信号量不区分优先级 | 中 | `general_chat` 可能阻塞 `human_escalation` |
| FreeSWITCH ESL 异常被 `pass` 吞掉 | 高 | 无日志、无重试，永久模拟模式 |
| FreeSWITCH 连接参数硬编码 | 中 | 密码 `"ClueCon"` 等未使用 settings |
| `get_llm()` 缓存无失效 | 低 | API Key 轮换后旧实例仍使用旧密钥 |
| LLM 无超时配置 | 中 | 智谱 API 慢时请求无限等待，占用信号量槽位 |

### `app/agent/schemas.py` — 结构化输出四层降级

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| 四层降级延迟累积 | 中 | 最坏情况 4×LLM_TIMEOUT，用户端可能超时 |
| 返回 None 时静默失败 | 中 | IntentClassification 返回 None 可能导致未定义状态 |
| `tool_choice` 格式可能被 API 拒绝 | 中 | 智谱兼容模式可能不支持此格式 |
| `safe_parse_model` 正则贪婪匹配 | 低 | 多 JSON 对象时会匹配过大范围 |
| Layer1/2 对智谱可能等价 | 低 | 两次相同调用浪费时间 |

### `app/memory/summarizer.py` — 对话摘要

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| Checkpoint 无限增长 | 高 | `RemoveMessage` 标记删除但原始 checkpoint 仍保留 |
| 摘要保存失败静默 | 中 | PG Store 不可用时摘要丢失，无告警 |
| Token 估算粗糙 | 中 | `len(content) // 2` 对中文严重低估 |
| 纯文本摘要丢失结构化数据 | 低 | `extra_data` 未保存，降低检索能力 |
| MIN_MESSAGES_TO_KEEP = 6 | 低 | 高频对话中可能不够覆盖完整上下文 |

### `app/memory/manager.py` — 记忆管理

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| read-modify-write 竞态 | 中 | 同用户并发会话丢失画像更新 |
| needs_escalation 时 2读2写 | 低 | 高并发下对 PG 产生不必要压力 |
| `load_recent_summaries` 搜索效率 | 低 | `query="recent"` 硬编码，语义搜索可能返回不相关结果 |
| 记忆加载失败返回空值 | 中 | PG Store 长期不可用时所有用户被视为新用户，无告警 |

### `app/tools/order_query.py` — 订单查询

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| 模拟数据无抽象层 | 中 | 切换真实数据源需完全重写 |
| 无缓存和超时 | 中 | 外部 API 慢时工具无限等待 |
| `order_id` 无格式校验 | 低 | LLM 可能传入无效 ID |
| 返回 dict 而非 Pydantic 模型 | 低 | 字段名拼写错误不会在开发时发现 |

### `app/tools/refund.py` — 退款工具

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| `_MOCK_REFUNDS` 无限增长 | 中 | 模拟数据永不清理，内存泄漏 |
| `uuid.uuid4().hex[:10]` 碰撞风险 | 低 | 截断 UUID 破坏唯一性保证 |
| `create_refund` 无幂等性 | 中 | 同订单可重复创建退款 |
| `refund_type` 无枚举校验 | 低 | LLM 可能传入任意字符串 |
| 工单未存储 | 低 | 创建后无法查询状态 |

### `app/tools/human_escalation.py` — 转人工

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| 队列位置硬编码 | 高 | 无真实排队逻辑，100人转人工看到的排队位置一样 |
| 无会话状态更新 | 高 | 转人工后下一条消息仍由 AI 处理 |
| reason 完全依赖 LLM | 中 | 转人工原因可能不准确 |
| 无回调/通知机制 | 中 | 不会推送到客服工作台 |

### `app/tools/phone_call.py` — 电话外呼

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| 手机号验证过于简单 | 低 | 不验证号段，`10000000000` 可通过 |
| 异常信息可能暴露内部地址 | 中 | `str(e)` 可能包含 FreeSWITCH 内部信息 |
| 状态值判断不完整 | 低 | 可能遗漏 `"error"`, `"rejected"` 等状态 |
| 无频率限制 | 中 | Agent 可能为同一用户反复拨打电话 |

### `app/tools/sms.py` — 短信工具

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| 阿里云 SDK 同步调用 | **高** | 阻塞事件循环，高并发下全局卡顿 |
| 腾讯云 SDK 同步调用 | **高** | 同上 |
| 每次创建新 SDK 客户端 | 中 | 无复用，浪费 HTTP 连接 |
| `send_custom_sms` 无内容审核 | 高 | LLM 可向任意手机号发送任意内容 |
| 模板渲染失败静默降级 | 低 | 用户可能收到不可读短信 |

### `app/api/middleware.py` — 限流中间件

| 问题 | 严重程度 | 描述 |
|------|---------|------|
| Redis 不可用时限流完全失效 | 高 | `pass` 静默吞异常，无告警 |
| 限流键只用 IP | 中 | 代理用户被误限流，代理池可绕过 |
| `request.client.host` 可能返回 `"unknown"` | 中 | 所有请求共享键，导致一起被限流 |
| `incr + expire` 非原子 | 中 | crash 后 key 永不过期，IP 被永久限流 |
| CORS `allow_origins=["*"]` + `allow_credentials=True` | 高 | CSRF 攻击风险 |
| 限流只覆盖 `/api/v1/chat` | 低 | 其他 API 路径无限流保护 |
