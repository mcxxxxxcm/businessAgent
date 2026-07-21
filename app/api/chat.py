"""SSE流式聊天接口"""

import asyncio
import json
import uuid
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.models.schemas import ChatRequest, ChatResponse, ResponseMeta
from app.memory.cache import SessionCache
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])

# 高风险工具的中文名(用于前端展示)
HIGH_RISK_TOOL_NAMES = {
    "create_refund": "创建退款申请",
    "create_service_ticket": "创建售后工单",
    "place_phone_call": "拨打电话",
    "send_custom_sms": "发送短信",
}


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
            "sub_intents": [],
            "current_sub_idx": 0,
            "sub_results": [],
            "orchestrator_event": None,
        }

        # 更新用户在线状态
        await SessionCache.set_user_online(request.user_id, session_id)

        # 发送会话信息
        yield {
            "event": "session",
            "data": json.dumps({"session_id": session_id, "user_id": request.user_id}, ensure_ascii=False),
        }

        try:
            # 使用graph.stream()支持interrupt_before暂停
            # stream_mode=["messages", "updates"]:
            #   messages: LLM token级流式输出 (chunk, metadata)
            #   updates: 节点输出更新 {node_name: output_dict}
            # 关键: stream()会在interrupt_before节点前暂停，astream_events不会!
            response_meta_data = None
            last_event_time = asyncio.get_event_loop().time()
            current_subtask_id = None
            is_orchestration_mode = False
            last_known_subtask_id = None
            current_running_node = None

            SUB_AGENT_NODE_NAMES = {"order_agent", "product_agent", "refund_agent", "knowledge_agent", "escalation"}
            TRACKED_NODES = {
                "intent_router", "order_agent", "product_agent", "refund_agent",
                "knowledge_agent", "escalation", "response", "task_orchestrator",
            }

            async for event in graph.astream(
                input_data,
                config=config,
                stream_mode=["messages", "updates"],
                version="v2",
            ):
                # version="v2" yields dict: {"type": "messages"|"updates", "ns": tuple, "data": payload}
                if not isinstance(event, dict):
                    continue
                stream_mode = event.get("type", "")
                data = event.get("data")

                # === messages模式: LLM token级流式 ===
                if stream_mode == "messages":
                    # data = (chunk, metadata)
                    if not isinstance(data, tuple) or len(data) != 2:
                        continue
                    chunk, metadata = data

                    # 过滤internal标记的LLM调用
                    tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
                    if "internal" in tags:
                        continue

                    content = getattr(chunk, "content", None)
                    if not content:
                        continue
                    # 处理content为list的情况(智谱API返回 [{"type":"text","text":"..."}])
                    if isinstance(content, list):
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
                            elif isinstance(item, str):
                                text_parts.append(item)
                        content = "".join(text_parts)
                    if isinstance(content, str) and content:
                        last_event_time = asyncio.get_event_loop().time()

                        # === 判断subtask_stream vs final_stream ===
                        use_subtask_stream = False
                        sub_id = current_subtask_id

                        if current_subtask_id:
                            use_subtask_stream = True
                        elif is_orchestration_mode:
                            langgraph_node = metadata.get("langgraph_node", "") if isinstance(metadata, dict) else ""
                            is_sub_agent = (
                                (current_running_node in SUB_AGENT_NODE_NAMES)
                                or (langgraph_node in SUB_AGENT_NODE_NAMES)
                            )
                            if is_sub_agent:
                                use_subtask_stream = True
                                sub_id = last_known_subtask_id

                        evt_name = "subtask_stream" if use_subtask_stream else "final_stream"
                        logger.info(
                            "SSE %s: sub_id=%s, running_node=%s, orchestration=%s, len=%d",
                            evt_name, sub_id, current_running_node, is_orchestration_mode, len(content),
                        )
                        yield {
                            "event": evt_name,
                            "data": json.dumps(
                                {"id": sub_id, "content": content} if sub_id
                                else {"content": content},
                                ensure_ascii=False,
                            ),
                        }

                # === updates模式: 节点输出更新 ===
                elif stream_mode == "updates":
                    # data = {node_name: output_dict}
                    if not isinstance(data, dict):
                        continue
                    for node_name, output in data.items():
                        if not isinstance(output, dict):
                            continue

                        # 节点开始通知
                        if node_name in TRACKED_NODES:
                            current_running_node = node_name
                            logger.info("node_update: node=%s (orchestration=%s)", node_name, is_orchestration_mode)
                            yield {
                                "event": "node_start",
                                "data": json.dumps({"node": node_name}, ensure_ascii=False),
                            }

                        # response节点的meta数据
                        if node_name == "response" and output.get("response_meta"):
                            response_meta_data = output["response_meta"]

                        # tool_executor节点 → 工具调用通知
                        if node_name.startswith("tool_executor_"):
                            # 从output中提取工具调用信息
                            messages_out = output.get("messages", [])
                            for msg in messages_out:
                                if hasattr(msg, "type") and msg.type == "tool":
                                    tool_name = getattr(msg, "name", "unknown")
                                    last_event_time = asyncio.get_event_loop().time()
                                    is_error = hasattr(msg, "status") and getattr(msg, "status", "") == "error"
                                    yield {
                                        "event": "tool_end",
                                        "data": json.dumps({
                                            "tool": tool_name,
                                            "status": "error" if is_error else "completed",
                                        }, ensure_ascii=False),
                                    }

                        # task_orchestrator编排事件
                        if node_name in ("task_orchestrator", "task_orchestrator_node"):
                            # 解析事件: 支持multi_event包装和单事件
                            evt_raw = output.get("orchestrator_event")
                            if evt_raw and isinstance(evt_raw, dict) and evt_raw.get("type") == "multi_event":
                                evts = evt_raw.get("events", [])
                            elif evt_raw:
                                evts = [evt_raw]
                            else:
                                evts = []

                            for evt in evts:
                                if not isinstance(evt, dict):
                                    continue
                                evt_type = evt.get("type")
                                last_event_time = asyncio.get_event_loop().time()
                                logger.info("orchestrator event: type=%s, keys=%s", evt_type, list(evt.keys()))

                                if evt_type == "subtask_start":
                                    current_subtask_id = evt["id"]
                                    last_known_subtask_id = evt["id"]
                                    yield {
                                        "event": "subtask_start",
                                        "data": json.dumps({
                                            "id": evt["id"],
                                            "title": evt.get("title", ""),
                                            "agent": evt.get("agent", ""),
                                        }, ensure_ascii=False),
                                    }
                                elif evt_type == "subtask_end":
                                    current_subtask_id = None
                                    yield {
                                        "event": "subtask_end",
                                        "data": json.dumps({
                                            "id": evt["id"],
                                            "status": evt.get("status", "success"),
                                            "summary": evt.get("summary", ""),
                                        }, ensure_ascii=False),
                                    }
                                elif evt_type == "plan":
                                    is_orchestration_mode = True
                                    yield {
                                        "event": "subtask_plan",
                                        "data": json.dumps({
                                            "total": evt["total"],
                                            "tasks": evt["tasks"],
                                        }, ensure_ascii=False),
                                    }

                # SSE心跳
                now = asyncio.get_event_loop().time()
                if now - last_event_time > settings.SSE_PING_INTERVAL:
                    last_event_time = now
                    yield {"event": "ping", "data": ""}

        except Exception as e:
            import traceback
            logger.error("流式生成失败: %s\n%s", e, traceback.format_exc())
            yield {
                "event": "error",
                "data": json.dumps({"error": f"生成回复时出错: {str(e)}"}, ensure_ascii=False),
            }

        # === HITL: 检查图是否因interrupt而暂停 ===
        try:
            from app.agent.graph import HIGH_RISK_TOOL_NODES
            state_snapshot = await graph.aget_state(config)
            next_nodes = state_snapshot.next or ()
            logger.info(
                "HITL检查: tasks=%d, next=%s",
                len(state_snapshot.tasks) if state_snapshot.tasks else 0,
                next_nodes,
            )
            # 判断interrupt: next指向高风险ToolNode 或 task有interrupts
            is_interrupt = False
            # 方式1: LangGraph 1.x — next指向interrupt_before的节点
            if any(n in HIGH_RISK_TOOL_NODES for n in next_nodes):
                is_interrupt = True
            # 方式2: 旧版 — task.interrupts非空
            if not is_interrupt and state_snapshot.tasks:
                for task in state_snapshot.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        is_interrupt = True
                        break

            if is_interrupt:
                # 图因interrupt暂停 — 推送确认请求给前端
                messages = state_snapshot.values.get("messages", [])
                tool_name = ""
                tool_args = {}
                if messages:
                    last_msg = messages[-1]
                    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                        tc = last_msg.tool_calls[0]
                        tool_name = tc.get("name", "")
                        tool_args = tc.get("args", {})

                tool_cn = HIGH_RISK_TOOL_NAMES.get(tool_name, tool_name or "高风险操作")

                logger.info(
                    "HITL interrupt: session=%s, tool=%s, next=%s, 等待用户确认",
                    session_id, tool_name, next_nodes,
                )
                yield {
                    "event": "interrupt",
                    "data": json.dumps({
                        "session_id": session_id,
                        "tool_name": tool_name,
                        "tool_display_name": tool_cn,
                        "tool_args": tool_args,
                        "message": f"即将执行: {tool_cn}，请确认是否继续？",
                    }, ensure_ascii=False),
                }
                # 流暂停，等待用户确认后再恢复(通过 /chat/confirm 接口)
                return
        except Exception as e:
            logger.warning("HITL状态检查失败(不影响已输出内容): %s", e)

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


# ============================================================
# HITL: 高风险操作确认接口
# ============================================================

class ToolConfirmRequest(BaseModel):
    """高风险工具确认请求"""
    session_id: str = Field(..., description="会话ID")
    approved: bool = Field(..., description="True=确认执行，False=拒绝执行")


class ToolConfirmResponse(BaseModel):
    """高风险工具确认响应"""
    status: str = Field(..., description="confirmed/rejected/no_interrupt/error")
    message: str = Field(..., description="状态描述")


@router.post("/chat/confirm", response_model=ToolConfirmResponse)
async def confirm_tool_execution(request: ToolConfirmRequest) -> ToolConfirmResponse:
    """高风险工具确认/拒绝 — 用户对interrupt的操作做出响应

    流程:
    1. 前端收到SSE interrupt事件 → 展示确认弹窗
    2. 用户点击"确认" → POST /api/v1/chat/confirm {session_id, approved: true}
    3. 用户点击"拒绝" → POST /api/v1/chat/confirm {session_id, approved: false}
    4. 本接口使用Command(resume)恢复图执行
    """
    from langgraph.types import Command
    from langchain_core.messages import ToolMessage
    from app.api.deps import get_graph

    try:
        session_id = str(uuid.UUID(request.session_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id格式无效")

    try:
        graph = await get_graph()
    except Exception as e:
        logger.error("获取Graph实例失败: %s", e)
        return ToolConfirmResponse(status="error", message="服务暂时不可用")

    config = {"configurable": {"thread_id": session_id}}

    # 检查图是否确实处于interrupt状态
    try:
        state_snapshot = await graph.aget_state(config)
    except Exception as e:
        logger.error("获取图状态失败: %s", e)
        return ToolConfirmResponse(status="error", message="获取会话状态失败")

    # 验证是否真的有interrupt
    # 方式1: LangGraph 1.x — next指向interrupt_before的节点
    has_interrupt = False
    next_nodes = state_snapshot.next or ()
    try:
        from app.agent.graph import HIGH_RISK_TOOL_NODES
        if any(n in HIGH_RISK_TOOL_NODES for n in next_nodes):
            has_interrupt = True
    except ImportError:
        pass
    # 方式2: 旧版 — task.interrupts非空
    if not has_interrupt:
        for task in (state_snapshot.tasks or []):
            if hasattr(task, "interrupts") and task.interrupts:
                has_interrupt = True
                break

    if not has_interrupt:
        return ToolConfirmResponse(
            status="no_interrupt",
            message="当前没有待确认的操作",
        )

    if request.approved:
        # 用户确认 → 恢复图执行，工具正常调用
        logger.info("HITL确认: session=%s, 用户批准执行", session_id)
        try:
            # 使用Command(resume)恢复执行
            async for _ in graph.astream(
                Command(resume={"__approved__": True}),
                config=config,
            ):
                pass  # 消费流直到完成
        except Exception as e:
            logger.error("HITL恢复执行失败: %s", e)
            return ToolConfirmResponse(status="error", message=f"恢复执行失败: {str(e)[:100]}")

        return ToolConfirmResponse(
            status="confirmed",
            message="操作已确认并执行完成",
        )
    else:
        # 用户拒绝 → 向图中注入拒绝消息，让Agent知道用户拒绝了
        logger.info("HITL拒绝: session=%s, 用户拒绝执行", session_id)

        # 获取待执行的工具调用信息
        tool_call_id = ""
        tool_name = ""
        messages = state_snapshot.values.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                tc = last_msg.tool_calls[0]
                tool_call_id = tc.get("id", "")
                tool_name = tc.get("name", "")

        tool_cn = HIGH_RISK_TOOL_NAMES.get(tool_name, tool_name or "操作")

        # 构造拒绝的ToolMessage，告诉Agent用户拒绝了
        reject_message = f"用户拒绝执行{tool_cn}。请向用户说明操作已取消，并询问是否需要其他帮助。"

        # 使用Command(resume) + 传入拒绝信息
        try:
            async for _ in graph.astream(
                Command(resume=ToolMessage(
                    content=reject_message,
                    tool_call_id=tool_call_id,
                )),
                config=config,
            ):
                pass
        except Exception as e:
            logger.error("HITL拒绝恢复失败: %s", e)
            return ToolConfirmResponse(status="error", message=f"拒绝操作失败: {str(e)[:100]}")

        return ToolConfirmResponse(
            status="rejected",
            message="操作已拒绝",
        )
