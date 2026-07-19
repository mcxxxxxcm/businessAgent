# CHANGELOG

## [2026-07-12] 深度安全审查修复 — 认证/PII/XSS/供应链

### 🔴 Critical修复

#### #1 全API无认证 → JWT认证体系
- **新增**: `app/core/auth.py` — JWT创建/验证/依赖注入 + PII脱敏函数(mask_phone/mask_pii)
- **新增**: `app/api/auth.py` — 登录接口(POST /api/v1/auth/login) + 令牌验证(GET /api/v1/auth/verify)
- **新增**: `requirements.txt` PyJWT依赖
- **修改**: `app/core/config.py` — 新增JWT_SECRET_KEY/JWT_ALGORITHM/JWT_EXPIRE_MINUTES/AUTH_ENABLED
- **修改**: `app/main.py` — 注册auth_router + 启动校验JWT_SECRET_KEY

#### #2 Session劫持 → session_id服务端生成+UUID格式校验
- **修改**: `app/api/chat.py` — session_id由服务端UUID生成，客户端传入时验证UUID格式

### 🟡 High修复

#### #3 DOM XSS → escapeHtml转义escalation_message
- **修改**: `app/static/index.html` — escalation_message通过escapeHtml()转义后再innerHTML

#### #4 手机号PII泄露 → 返回值脱敏
- **修改**: `app/tools/phone_call.py` — 返回值中手机号脱敏为138****5678
- **修改**: `app/tools/sms.py` — 3个短信工具返回值中手机号脱敏

#### #5 调试接口暴露 → 生产环境禁用
- **修改**: `app/api/sessions.py` — /state接口在DEBUG=False时返回404

### 🟠 Medium修复

#### #6 依赖无上界 → 锁定major version上界
- **修改**: `requirements.txt` — 所有依赖加`<N.0.0`上界(LangChain严格锁`<0.4.0`)

#### #7 user_id无限制 → max_length=64
- **修改**: `app/models/schemas.py` — ChatRequest.user_id和FeedbackRequest.user_id加max_length=64

#### #8 Feedback无限流 → 同session每分钟5次
- **修改**: `app/api/feedback.py` — Redis计数防刷，超限返回received=False

#### #9 knowledge_rag逐字符匹配 → 逐词匹配
- **修改**: `app/tools/knowledge_rag.py` — `for word in query_lower`改为`query_lower.split()`

---

## [2026-07-12] 对抗性审查修复 — 3个月后失效模式

### 🔴 高优先级修复

#### #1 Semaphore无背压 → OOM Kill
- **文件**: `app/api/deps.py`, `app/core/config.py`
- **问题**: `llm_semaphore = asyncio.Semaphore(5)` 无超时无拒绝，50并发请求静默排队导致OOM
- **修复**: 
  - 新增 `LLM_QUEUE_TIMEOUT` 配置(默认30秒)
  - 新增 `LLM_MAX_CONCURRENT` 配置(默认5)
  - `acquire_llm_semaphore()` 函数：带超时的信号量获取，超时抛 `LLMQueueTimeoutError`
  - 所有节点改用 `acquire_llm_semaphore()` 替代 `async with llm_semaphore`

#### #2 API_KEY空值 + 模型不可达 → 全站静默失效
- **文件**: `app/main.py`, `app/core/config.py`
- **问题**: `ZHIPU_API_KEY` 默认空字符串，系统启动不报错但所有LLM请求401
- **修复**:
  - lifespan中新增 `_validate_llm_config()`: API_KEY为空时crash启动
  - 新增 `LLM_STARTUP_CHECK` 配置(默认True): 启动时发一条测试请求验证模型可达
  - 可达性检查失败时crash并输出明确错误信息

#### #3 Redis无maxmemory → 内存满后拒绝写入
- **文件**: `docker-compose.yml`
- **问题**: Redis默认 `noeviction`，内存满后限流key写入失败→限流失效
- **修复**: docker-compose中Redis加 `--maxmemory 256mb --maxmemory-policy allkeys-lru`

#### #4 降级链无监控 → Layer4命中率50%没人知道
- **文件**: `app/agent/schemas.py`, `app/memory/cache.py`
- **问题**: 四层降级链每层成功/失败只有debug日志，无法感知劣化趋势
- **修复**:
  - `structured_llm_output()` 每层结果写入Redis计数器 `stats:degradation:{schema}:layer{N}:{ok|fail}`
  - 新增 `cache.get_degradation_stats()` 方法，聚合各层命中率
  - health接口暴露降级统计，Layer4命中率>50%时health返回degraded

### 🟡 中优先级修复

#### #5 SSE Ghost连接 → 资源浪费10%
- **文件**: `app/api/chat.py`, `app/core/config.py`
- **问题**: 用户关闭浏览器但服务端不知道，LLM调用+checkpoint写入继续执行
- **修复**:
  - 新增 `SSE_PING_INTERVAL` 配置(默认15秒)
  - event_generator中加心跳ping：无token输出时每15秒发 `:ping` SSE注释
  - 客户端断开时SSEStarlette自动触发GeneratorExit，已有try/except覆盖

#### #6 Checkpoint加载失败 → 用户失忆
- **文件**: `app/api/chat.py`, `app/core/config.py`
- **问题**: PG偶发连接超时→Checkpoint加载失败→历史对话全丢
- **修复**:
  - `graph.astream_events()` 加外层超时 `asyncio.wait_for(timeout=60)`
  - 新增 `GRAPH_EXECUTION_TIMEOUT` 配置(默认60秒)
  - 超时时返回友好错误而非无限挂起

#### #8 摘要截断200字 → 长对话信息丢失
- **文件**: `app/memory/summarizer.py`
- **问题**: `str(msg.content)[:200]` 截断订单详情等长内容，摘要丢失关键信息
- **修复**:
  - ToolMessage不截断(工具返回值必须完整保留)
  - AI消息截断上限提升到500字
  - HumanMessage保持200字(用户消息通常较短)

#### #9 Checkpoint清理SQL慢 → 清理卡住
- **文件**: `app/memory/checkpointer.py`
- **问题**: 3个月后checkpoints表百万行，GROUP BY全表扫描导致清理>30秒
- **修复**:
  - `created_at`列迁移时同步创建复合索引 `(thread_id, checkpoint_ns, created_at)`
  - keep_latest策略改用子查询+索引，避免全表GROUP BY
  - 新增 `checkpoint_id`上的索引加速DELETE

#### #10 限流降级计数器重启归零 → 监控盲区
- **文件**: `app/api/middleware.py`
- **问题**: `_rate_limit_fallback_count` 进程内变量，重启后归零
- **修复**: 改用Redis INCR持久化计数 `stats:rate_limit:fallback_count`，进程内变量作为缓存

---

## [2026-07-12] 上下文管理优化

### P0修复
- 子Agent记忆注入断裂: 新增 `build_agent_prompt_input()` 统一函数
- 全量消息传入: `MAX_AGENT_HISTORY=10` 限制

### P1修复
- 记忆无优先级: `detailed=False` 精简模式
- 摘要触发滞后: intent_router入口预检

### P2修复
- 窗口过窄: 动态窗口(≤8全取，>8取最近8条)

---

## [2026-07-12] 第二三梯队漏洞修复

### 第二梯队
- #4 Redis静默降级: pass→logger.error+降级计数器+RATE_LIMIT_REDIS_FALLBACK配置
- #5 限流非原子: incr+expire→Lua脚本
- #6 CORS漏洞: 白名单+credentials配置化

### 第三梯队
- #7 画像竞态: merge模式(计数器取max, 列表合并去重)
- #8 四层降级超时: 每层8秒wait_for+NotImplementedError跳Layer2
- #9 Token估算: 中文1.5token/字+英文4字符/token

---

## [2026-07-11] 第一梯队漏洞修复

- #1 Checkpoint膨胀: 双策略清理(keep_latest+TTL)+后台asyncio.Task
- #2 SMS阻塞事件循环: asyncio.to_thread()+10秒超时
- #3 单例竞态: async_init_singleton()统一函数

---

## [2026-07-11] HITL + 错误处理 + Agent限制

- HITL反馈机制: 前端按钮→API→Redis→转人工
- ToolNode拆分: 5个独立ToolNode+handle_tool_errors=True
- ReAct步数限制: react_step_count/max_react_steps双重保护
- 子Agent try-catch: LLM调用异常返回安全AIMessage
- 低置信度转人工: confidence<0.5显示转人工按钮
