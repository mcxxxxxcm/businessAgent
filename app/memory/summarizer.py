"""对话摘要压缩 - 控制长对话的Token消耗"""

import logging

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, RemoveMessage
from langgraph.store.base import BaseStore

from app.agent.state import CustomerServiceState

logger = logging.getLogger(__name__)

# 保留最近N条消息，超过则压缩早期消息
MAX_MESSAGES = 20


async def summarize_if_needed(
    state: CustomerServiceState,
    *,
    store: BaseStore,
) -> dict:
    """当消息超过阈值时，压缩早期消息为摘要

    这是一个可选的后处理步骤，可在图的response节点之后调用，
    用于控制长对话的上下文窗口大小和Token消耗。

    Args:
        state: 当前Agent状态
        store: 长期记忆Store

    Returns:
        包含RemoveMessage指令的state更新
    """
    messages = state["messages"]

    if len(messages) <= MAX_MESSAGES:
        return {}

    from app.api.deps import get_llm

    llm = get_llm()

    # 对早期消息做摘要
    old_messages = messages[:-MAX_MESSAGES]
    summary_prompt = (
        "请将以下客服对话历史压缩为简短摘要，保留关键信息"
        "(用户问题、已执行操作、关键结论、用户偏好):\n\n"
    )

    for msg in old_messages:
        if isinstance(msg, HumanMessage):
            summary_prompt += f"用户: {msg.content}\n"
        elif isinstance(msg, AIMessage):
            content = msg.content[:200] if msg.content else ""
            summary_prompt += f"助手: {content}\n"

    try:
        summary_result = await llm.ainvoke([SystemMessage(content=summary_prompt)])

        # 存入长期记忆
        user_id = state.get("user_id", "anonymous")
        ns = ("users", user_id, "summary")
        turn_count = state.get("turn_count", 0)

        await store.aput(
            ns,
            f"summary_{turn_count}",
            {
                "text": summary_result.content,
                "turn_count": turn_count,
                "message_count": len(old_messages),
            },
        )

        logger.info("对话摘要已保存: user=%s, turn=%d, compressed=%d messages", user_id, turn_count, len(old_messages))

    except Exception as e:
        logger.warning("对话摘要生成失败: %s", e)
        return {}

    # 标记删除旧消息
    remove_messages = [RemoveMessage(id=m.id) for m in old_messages if hasattr(m, "id") and m.id]
    return {"messages": remove_messages}
