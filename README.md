# 智能客服Agent

生产级电商智能客服Agent，基于LangChain/LangGraph框架构建。

## 特性

- 🤖 **意图路由**: 自动分类用户意图，路由到对应专业Agent
- 🔧 **5大工具**: 订单查询、商品搜索、退货退款、知识库RAG、转人工
- 🧠 **三层记忆**: Redis热缓存 + PG对话持久化 + PG跨会话长期记忆
- ⚡ **流式响应**: SSE逐Token推送，快速响应体验
- 🚀 **高并发**: 全异步架构，连接池管理，限流保护
- 🐳 **一键部署**: Docker Compose (API + PostgreSQL + Redis)

## 技术栈

| 组件 | 选型 |
|------|------|
| LLM | 智谱GLM-4 (OpenAI兼容模式) |
| Agent | LangGraph StateGraph |
| API | FastAPI + SSE |
| 短期记忆 | AsyncPostgresSaver |
| 长期记忆 | AsyncPostgresStore |
| 热缓存 | Redis |
| RAG | PGVector + DashScope Embeddings |
| 部署 | Docker Compose |

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
cd 智能客服

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate  # Windows

# 安装依赖
pip install -e .
```

### 2. 配置环境变量

```bash
cp .env .env
# 编辑.env，填入你的智谱API Key
```

### 3. 启动基础设施

```bash
docker-compose up -d postgres redis
```

### 4. 初始化数据库

```bash
python scripts/init_db.py
```

### 5. 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. 测试

```bash
# 健康检查
curl http://localhost:8000/health

# 非流式聊天
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好", "user_id": "test_user"}'

# 流式聊天
curl -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "查询订单ORD20250101001", "user_id": "test_user"}'
```

## Docker部署

```bash
# 一键启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f api
```

## API接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/chat | 非流式聊天 |
| POST | /api/v1/chat/stream | SSE流式聊天 |
| GET | /api/v1/sessions/{id}/history | 获取会话历史 |
| GET | /api/v1/sessions/{id}/state | 获取会话状态 |
| DELETE | /api/v1/sessions/{id} | 结束会话 |
| GET | /health | 健康检查 |

## Agent架构

```
START → intent_router → [order_agent | product_agent | refund_agent | knowledge_agent | escalation | response]
                           → tool_executor(如有工具调用) → response → END
```

## 项目结构

```
app/
├── agent/          # LangGraph Agent核心
│   ├── graph.py    # StateGraph构建
│   ├── state.py    # 状态定义
│   ├── prompts.py  # System Prompt
│   └── nodes/      # 各Agent节点
├── tools/          # Agent工具
├── memory/         # 记忆系统
├── rag/            # 知识库RAG
├── api/            # FastAPI接口
├── core/           # 配置/日志/异常
├── models/         # 数据模型
└── services/       # 业务服务层
```

## License

MIT
