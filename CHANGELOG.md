# CHANGELOG

## [2026-07-20] 自动手风琴折叠UI — 重构SSE事件协议

### 核心改动
- SSE事件协议重构: `token` → `final_stream`, 新增 `subtask_start`/`subtask_stream`/`subtask_end`
- 前端状态驱动: 收到`subtask_start`自动折叠之前任务+展开当前, `subtask_end`折叠当前+显示摘要
- 子任务流式内容: 子Agent的LLM token通过`subtask_stream`事件追加到对应子任务卡片内
- 最终回复独立渲染: `final_stream`事件追加到`.text-area`区域,不覆盖子任务面板

### 后端
- `app/api/chat.py`: 事件协议重构, 新增`current_subtask_id`状态追踪区分subtask/final
- `app/agent/graph.py`: `task_orchestrator_node`输出`subtask_start`/`subtask_end`事件替代`progress`/`complete`

### 前端
- `app/static/index.html`: 新增`handleSubtaskStart`/`handleSubtaskStream`/`handleSubtaskEnd`函数
- `app/static/index.html`: 删除旧的`updateTaskProgress`/`toggleTaskResult`/`node_start`处理
- CSS优化: 删除进度条,新增error状态样式,子任务卡片hover效果

---

## [2026-07-20] Bug修复 — llm_semaphore未导入 + Prompt花括号转义 + 退款Prompt

### 修复
- `app/agent/nodes/response.py`: _extract_response_meta中补导llm_semaphore(NameError)
- `app/agent/prompts.py`: MULTI_INTENT_INSTRUCTION中JSON示例花括号双写转义({{id:1}})
- `app/agent/prompts.py`: REFUND_AGENT_INSTRUCTION改为"直接调用create_refund工具，系统自动弹出确认框"，避免LLM文字拒绝
- `app/api/chat.py`: input_data补sub_intents/current_sub_idx/sub_results初始值
- `app/api/chat.py`: on_chat_model_stream增加防御性content解析(list/dict/str)
- `app/api/chat.py`: 错误日志增加完整traceback

---

## [2026-07-20] 多意图编排动态进度条 + 折叠展开子任务结果

### 新增

#### 后端
- `app/agent/state.py`: 新增 `orchestrator_event` 字段，task_orchestrator每次执行写入事件(plan/progress/complete)
- `app/agent/graph.py`: task_orchestrator_node写入编排事件 — 首次推送plan(任务列表)，每次子意图完成推送progress(结果+进度)，全部完成推送complete
- `app/api/chat.py`: SSE流中监听task_orchestrator的on_chain_end，推送3种SSE事件: `sub_intent_plan`(任务规划)、`sub_intent_progress`(子任务完成)、`sub_intent_complete`(全部完成)

#### 前端
- `app/static/index.html` CSS: 新增 `.task-plan` 进度卡片、`.task-item` 状态行(pending/running/done)、`.task-progress-bar` 进度条、`.task-result` 折叠展开动画
- `app/static/index.html` JS: `renderTaskPlan()` 渲染任务列表、`updateTaskProgress()` 更新进度+折叠、`toggleTaskResult()` 展开/折叠切换
- 子任务完成时自动展开1.5秒再折叠，用户可手动点击展开查看详细结果
- 最终Agent回复不折叠，直接展示在任务卡片下方

---

## [2026-07-20] 多意图拆解 + 串行编排 — 支持长问题多子问题处理

### 核心变更

#### 架构: 单意图/多意图双路径
- **单意图(80%场景)**: `intent_router → subAgent → response` — 原路径不变，零开销
- **多意图(20%场景)**: `intent_router → task_orchestrator → subAgent → task_orchestrator → ... → response` — 串行编排循环

#### 文件修改清单

| 文件 | 修改 |
|------|------|
| `app/agent/schemas.py` | 新增 `SubIntent`(id/intent/depends_on/tool_hint) + `MultiIntentDecomposition`(intents/confidence/reasoning) |
| `app/agent/state.py` | 新增 `sub_intents`/`current_sub_idx`/`sub_results` 3个字段 |
| `app/agent/prompts.py` | 新增 `MULTI_INTENT_PROMPT` — 意图拆解Prompt，含拆解规则和示例 |
| `app/agent/nodes/intent_router.py` | 重写：先MultiIntentDecomposition拆解→单意图走原路径→多意图走编排→拆解失败回退单意图 |
| `app/agent/graph.py` | 新增 `task_orchestrator_node`(串行编排+结果收集)+`_route_after_agent_unified`(自动判断单/多意图) |
| `app/agent/edges.py` | `route_by_intent`新增多意图优先级 + 新增`route_after_orchestrator`(编排后路由) |
| `app/static/index.html` | node_start事件中展示"正在处理多个问题..."进度提示 |

#### 关键设计决策

1. **置信度阈值防误拆**: `MULTI_INTENT_CONFIDENCE_THRESHOLD=0.6`，低于阈值当单意图处理
2. **串行而非并行**: DAG并行与LangGraph静态图冲突，串行+SSE流式输出用户感知不差
3. **前序结果注入**: 后续子意图通过`[前序处理结果]`上下文获取前序子意图的输出
4. **结果汇总**: 所有子意图完成后，结果注入`conversation_summary`供response节点汇总回复
5. **单意图零开销**: sub_intents为空时完全走原路径，不影响80%的正常对话

---

## [2026-07-20] HITL: 高风险操作人工确认机制

### 🔴 Critical新增

#### 高风险工具执行前需用户确认(interrupt_before)
- **修改**: `app/agent/graph.py` — `compile_graph()`新增`interrupt_before=HIGH_RISK_TOOL_NODES`，图编译时对`tool_executor_refund_agent`和`tool_executor_escalation`设置执行前中断
- **修改**: `app/agent/graph.py` — 新增`HIGH_RISK_TOOL_NODES`和`HIGH_RISK_TOOL_NAMES`常量，定义需确认的ToolNode和高风险工具中文名
- **修改**: `app/api/chat.py` — SSE流结束后检查图`aget_state`是否因interrupt暂停，是则推送`interrupt`事件(含工具名、参数、确认消息)给前端
- **新增**: `POST /api/v1/chat/confirm` — 用户确认/拒绝接口，使用`Command(resume)`恢复图执行
  - 确认: `Command(resume={"__approved__": True})` → 工具正常执行
  - 拒绝: `Command(resume=ToolMessage(content=拒绝消息))` → Agent收到拒绝反馈，告知用户操作已取消
- **修改**: `app/static/index.html` — 新增确认弹窗UI(参数展示+确认/取消按钮)，处理SSE`interrupt`事件，调用`/chat/confirm`接口

#### 高风险工具清单
| 工具 | 风险级别 | 需确认 | 原因 |
|------|---------|--------|------|
| `create_refund` | 高 | ✅ | 创建退款申请，不可逆操作 |
| `create_service_ticket` | 高 | ✅ | 创建售后工单，触发人工流程 |
| `place_phone_call` | 高 | ✅ | 拨打电话，骚扰风险 |
| `send_custom_sms` | 高 | ✅ | 发送自定义短信，垃圾短信风险 |
| `query_order` | 低 | ❌ | 只读查询 |
| `track_logistics` | 低 | ❌ | 只读查询 |
| `search_products` | 低 | ❌ | 只读查询 |
| `check_inventory` | 低 | ❌ | 只读查询 |
| `query_refund_status` | 低 | ❌ | 只读查询 |
| `send_order_notification` | 中 | ❌ | 模板短信，内容可控 |
| `send_refund_notification` | 中 | ❌ | 模板短信，内容可控 |
| `search_knowledge_base` | 低 | ❌ | 只读查询 |
| `transfer_to_human` | 低 | ❌ | 转人工是用户意图 |

---

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
