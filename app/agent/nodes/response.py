"""最终响应生成节点"""

from langchain_core.messages import SystemMessage
from langgraph.store.base import BaseStore

from app.agent.state import CustomerServiceState
from app.agent.prompts import RESPONSE_PROMPT


async def response_node(state: CustomerServiceState, *, store: BaseStore) -> dict:
    """最终响应节点 - 生成客服回复并更新长期记忆

    此节点作为所有子Agent的汇聚点，负责:
    1. 如果子Agent已生成回复(包含工具调用结果)，则直接返回
    2. 否则使用LLM生成通用回复
    3. 更新用户长期记忆(交互统计)
    """
    messages = state["messages"]
    last_message = messages[-1] if messages else None

    # 如果最后一条已经是AI消息且有内容，直接返回(子Agent已生成回复)
    if last_message and hasattr(last_message, "type") and last_message.type == "ai":
        if last_message.content and not getattr(last_message, "tool_calls", None):
            # AI已有回复内容，保存长期记忆后返回
            await _update_user_memory(state, store)
            return {}

    # 否则生成通用回复
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()

    system_content = RESPONSE_PROMPT.format(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
    )

    async with llm_semaphore:
        response = await llm.ainvoke(
            [
                SystemMessage(content=system_content),
                *state["messages"][-10:],  # 最近10条消息
            ]
        )

    # 保存长期记忆
    await _update_user_memory(state, store)

    return {"messages": [response]}


async def _update_user_memory(state: CustomerServiceState, store: BaseStore) -> None:
    """更新用户长期记忆"""
    user_id = state.get("user_id", "anonymous")
    ns = ("users", user_id, "stats")

    try:
        # 读取现有统计
        existing = await store.aget(ns, "interactions")
        stats = existing.value if existing else {"total_conversations": 0, "intents": {}}

        # 更新统计
        stats["total_conversations"] = stats.get("total_conversations", 0) + 1
        intent = state.get("intent", "unknown")
        intents = stats.get("intents", {})
        intents[intent] = intents.get(intent, 0) + 1
        stats["intents"] = intents

        await store.aput(ns, "interactions", stats)
    except Exception:
        # 记忆更新失败不影响主流程
        pass
