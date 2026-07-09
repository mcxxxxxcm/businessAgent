"""最终响应生成节点 - 生成客服回复并保存记忆"""

from langchain_core.messages import SystemMessage
from langgraph.store.base import BaseStore

from app.agent.state import CustomerServiceState
from app.agent.prompts import RESPONSE_PROMPT


async def response_node(state: CustomerServiceState, *, store: BaseStore) -> dict:
    """最终响应节点 - 生成客服回复并保存长期记忆

    此节点作为所有子Agent的汇聚点，负责:
    1. 如果子Agent已生成回复(包含工具调用结果)，则直接返回
    2. 否则使用LLM生成通用回复(注入记忆上下文)
    3. 保存用户长期记忆(画像、交互统计)
    """
    messages = state["messages"]
    last_message = messages[-1] if messages else None

    # 如果最后一条已经是AI消息且有内容，直接返回(子Agent已生成回复)
    if last_message and hasattr(last_message, "type") and last_message.type == "ai":
        if last_message.content and not getattr(last_message, "tool_calls", None):
            # AI已有回复内容，保存长期记忆后返回
            await _save_memory(state, store)
            return {}

    # 否则生成通用回复
    from app.api.deps import get_llm, llm_semaphore
    from app.memory.manager import format_memory_for_prompt
    from app.memory.profile import UserProfile

    llm = get_llm()

    # 构建记忆上下文
    profile_data = state.get("user_profile", {})
    profile = UserProfile(profile_data) if profile_data else None
    conversation_summary = state.get("conversation_summary", "")
    history_summary = state.get("history_summary", "")
    memory_text = format_memory_for_prompt(
        profile=profile,
        conversation_summary=conversation_summary,
        history_summary=history_summary,
    )

    system_content = RESPONSE_PROMPT.format(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
    )

    # 注入记忆上下文到System Prompt
    if memory_text:
        system_content += memory_text

    # 如果有当前对话摘要，也注入
    if conversation_summary:
        system_content += f"\n\n【当前对话摘要】\n{conversation_summary}"

    async with llm_semaphore:
        response = await llm.ainvoke(
            [
                SystemMessage(content=system_content),
                *state["messages"][-10:],  # 最近10条消息
            ],
            config={"tags": ["response"]},  # 标记为response节点输出，SSE只放行此tag
        )

    # 保存长期记忆
    await _save_memory(state, store)

    return {"messages": [response]}


async def _save_memory(state: CustomerServiceState, store: BaseStore) -> None:
    """保存记忆: 用户画像更新"""
    from app.memory.manager import save_memory_after_response

    try:
        await save_memory_after_response(store, state)
    except Exception as e:
        # 记忆保存失败不影响主流程
        import logging
        logging.getLogger(__name__).warning("保存记忆失败: %s", e)
