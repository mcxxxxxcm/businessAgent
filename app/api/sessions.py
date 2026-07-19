"""会话管理接口"""

import logging

from fastapi import APIRouter, HTTPException

from app.memory.cache import SessionCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.get("/{session_id}/history")
async def get_session_history(session_id: str):
    """获取会话历史 - 用于前端页面刷新后恢复

    通过LangGraph Checkpointer加载历史状态。
    """
    try:
        from app.api.deps import get_graph

        graph = await get_graph()

        # 通过checkpointer获取会话状态
        config = {"configurable": {"thread_id": session_id}}
        state = await graph.aget_state(config)

        if not state or not state.values:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在或已过期")

        messages = state.values.get("messages", [])
        history = []
        for msg in messages:
            msg_type = getattr(msg, "type", "unknown")
            content = getattr(msg, "content", "")
            if msg_type in ("human", "ai"):
                history.append({
                    "role": "user" if msg_type == "human" else "assistant",
                    "content": content,
                })

        return {
            "session_id": session_id,
            "messages": history,
            "intent": state.values.get("intent"),
            "turn_count": state.values.get("turn_count", 0),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取会话历史失败: %s", e)
        raise HTTPException(status_code=500, detail="获取会话历史失败")


@router.delete("/{session_id}")
async def end_session(session_id: str):
    """结束会话 - 清理Redis缓存"""
    await SessionCache.delete_session_context(session_id)
    return {"message": f"会话 {session_id} 已结束", "session_id": session_id}


@router.get("/{session_id}/state")
async def get_session_state(session_id: str):
    """获取会话完整状态(调试用) — 生产环境禁用"""
    from app.core.config import settings
    if not settings.DEBUG:
        raise HTTPException(status_code=404, detail="此接口仅在调试模式可用")
    try:
        from app.api.deps import get_graph

        graph = await get_graph()
        config = {"configurable": {"thread_id": session_id}}
        state = await graph.aget_state(config)

        if not state or not state.values:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

        # 返回状态摘要(排除messages以减少响应体积)
        values = state.values
        return {
            "session_id": session_id,
            "user_id": values.get("user_id"),
            "intent": values.get("intent"),
            "sentiment": values.get("sentiment"),
            "turn_count": values.get("turn_count", 0),
            "needs_escalation": values.get("needs_escalation", False),
            "message_count": len(values.get("messages", [])),
            "next": state.next,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取会话状态失败: %s", e)
        raise HTTPException(status_code=500, detail="获取会话状态失败")
