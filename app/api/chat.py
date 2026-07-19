"""SSE流式聊天接口"""

import asyncio
import json
import uuid
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import HumanMessage

from app.models.schemas import ChatRequest, ChatResponse, ResponseMeta
from app.memory.cache import SessionCache
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    """SSE流式聊天接口 - 逐Token输出Agent响应"""

    async def event_generator() -> AsyncGenerator[dict, None]:
        from app.api.deps import get_graph

        try:
            graph = await get_graph()
        except Exception as e:
            logger.error("获取Graph实例失败: %s", e)
            yield {
                "event": "error",
                "data": json.dumps({"error": "服务暂时不可用，请稍后重试"}, ensure_ascii=False),
            }
            return

        # session_id由服务端生成(防会话劫持/固定攻击)
        if request.session_id:
            try:
                session_id = str(uuid.UUID(request.session_id))
            except ValueError:
                yield {
                    "event": "error",
                    "data": json.dumps({"error": "session_id格式无效"}, ensure_ascii=False),
                }
                return
        else:
            session_id = str(uuid.uuid4())
        config = {
            "configurable": {
                "thread_id": session_id,
            }
        }

        input_data = {
            "messages": [HumanMessage(content=request.message)],
            "user_id": request.user_id,
            "session_id": session_id,
            "turn_count": 0,
            "max_turns": settings.MAX_CONVERSATION_TURNS,
            "needs_escalation": False,
            "active_agent": None,
            "conversation_summary": "",
            "user_profile": None,
            "history_summary": "",
            "response_meta": None,
            "react_step_count": 0,
            "max_react_steps": settings.MAX_REACT_STEPS,
        }

        # 更新用户在线状态
        await SessionCache.set_user_online(request.user_id, session_id)

        # 发送会话信息
        yield {
            "event": "session",
            "data": json.dumps({"session_id": session_id, "user_id": request.user_id}, ensure_ascii=False),
        }

        try:
            # 使用astream_events v3 API获取Token级流式输出
            # 同时跟踪response_meta用于流结束时推送
            response_meta_data = None
            last_event_time = asyncio.get_event_loop().time()

            async for event in graph.astream_events(
                input_data,
                config=config,
                version="v2",
            ):
                kind = event.get("event")

                # LLM生成Token — 过滤内部调用(意图路由JSON等)，只放行用户可见的回复
                if kind == "on_chat_model_stream":
                    tags = event.get("tags", [])
                    # 排除标记为internal的LLM调用(意图路由等)
                    if "internal" in tags:
                        continue

                    chunk = event["data"]["chunk"]
                    if hasattr(chunk, "content") and chunk.content:
                        if isinstance(chunk.content, str):
                            last_event_time = asyncio.get_event_loop().time()
                            yield {
                                "event": "token",
                                "data": json.dumps({"content": chunk.content}, ensure_ascii=False),
                            }

                # 工具调用开始
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    last_event_time = asyncio.get_event_loop().time()
                    yield {
                        "event": "tool_start",
                        "data": json.dumps(
                            {"tool": tool_name, "message": f"正在调用 {tool_name}..."},
                            ensure_ascii=False,
                        ),
                    }

                # 工具调用完成
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    # 检查工具是否返回错误
                    output = event.get("data", {}).get("output", "")
                    is_error = hasattr(output, "status") and getattr(output, "status", "") == "error"
                    last_event_time = asyncio.get_event_loop().time()
                    yield {
                        "event": "tool_end",
                        "data": json.dumps({
                            "tool": tool_name,
                            "status": "error" if is_error else "completed",
                        }, ensure_ascii=False),
                    }

                # 节点执行
                elif kind == "on_chain_start":
                    node_name = event.get("name", "")
                    tracked_nodes = {
                        "intent_router",
                        "order_agent",
                        "product_agent",
                        "refund_agent",
                        "knowledge_agent",
                        "escalation",
                        "response",
                    }
                    if node_name in tracked_nodes:
                        yield {
                            "event": "node_start",
                            "data": json.dumps({"node": node_name}, ensure_ascii=False),
                        }

                # 节点完成 — 捕获response节点的meta数据
                elif kind == "on_chain_end":
                    node_name = event.get("name", "")
                    if node_name == "response":
                        output = event.get("data", {}).get("output", {})
                        if isinstance(output, dict) and output.get("response_meta"):
                            response_meta_data = output["response_meta"]

                # SSE心跳: 长时间无token输出时发ping，检测Ghost连接
                now = asyncio.get_event_loop().time()
                if now - last_event_time > settings.SSE_PING_INTERVAL:
                    last_event_time = now
                    yield {"event": "ping", "data": ""}

        except Exception as e:
            logger.error("流式生成失败: %s", e)
            yield {
                "event": "error",
                "data": json.dumps({"error": f"生成回复时出错: {str(e)}"}, ensure_ascii=False),
            }

        # 流结束 — 推送response_meta(如有)
        done_data = {"session_id": session_id}
        if response_meta_data:
            done_data["response_meta"] = response_meta_data
        yield {
            "event": "done",
            "data": json.dumps(done_data, ensure_ascii=False),
        }

    return EventSourceResponse(event_generator())


@router.post("/chat", response_model=ChatResponse)
async def chat_sync(request: ChatRequest) -> ChatResponse:
    """非流式聊天接口 - 适用于不支持SSE的客户端"""
    from app.api.deps import get_graph

    try:
        graph = await get_graph()
    except Exception as e:
        logger.error("获取Graph实例失败: %s", e)
        raise

    # session_id由服务端生成(防会话劫持/固定攻击)
    if request.session_id:
        try:
            session_id = str(uuid.UUID(request.session_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="session_id格式无效")
    else:
        session_id = str(uuid.uuid4())
    config = {
        "configurable": {
            "thread_id": session_id,
        }
    }

    input_data = {
        "messages": [HumanMessage(content=request.message)],
        "user_id": request.user_id,
        "session_id": session_id,
        "turn_count": 0,
        "max_turns": settings.MAX_CONVERSATION_TURNS,
        "needs_escalation": False,
        "active_agent": None,
        "conversation_summary": "",
        "user_profile": None,
        "history_summary": "",
        "response_meta": None,
        "react_step_count": 0,
        "max_react_steps": settings.MAX_REACT_STEPS,
    }

    # 更新用户在线状态
    await SessionCache.set_user_online(request.user_id, session_id)

    try:
        result = await asyncio.wait_for(
            graph.ainvoke(input_data, config=config),
            timeout=settings.GRAPH_EXECUTION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("同步调用超时(%.0fs)", settings.GRAPH_EXECUTION_TIMEOUT)
        raise HTTPException(status_code=504, detail="请求处理超时，请稍后重试")
    except Exception as e:
        logger.error("同步调用失败: %s", e)
        raise

    # 提取最后一条AI消息
    last_ai_msg = ""
    for msg in reversed(result.get("messages", [])):
        if hasattr(msg, "type") and msg.type == "ai":
            last_ai_msg = msg.content
            break

    # 提取回复元数据
    meta_data = result.get("response_meta")
    response_meta = ResponseMeta(**meta_data) if meta_data else None

    return ChatResponse(
        session_id=session_id,
        reply=last_ai_msg,
        intent=result.get("intent"),
        sentiment=result.get("sentiment"),
        needs_escalation=result.get("needs_escalation", False),
        response_meta=response_meta,
    )
