"""记忆管理器 - 所有记忆操作的中央调度入口

统一管理三层记忆的读写，对外暴露简洁的API:
- 加载上下文: 在意图路由前加载用户画像+历史摘要
- 保存记忆: 在回复生成后更新画像+触发摘要
- 注入Prompt: 将记忆格式化为可注入System Prompt的文本
"""

import logging
from typing import Optional

from langgraph.store.base import BaseStore

from app.agent.state import CustomerServiceState
from app.memory.profile import (
    UserProfile,
    load_user_profile,
    update_profile_from_state,
    load_recent_summaries,
)
from app.memory.summarizer import should_summarize, summarize_conversation

logger = logging.getLogger(__name__)


# 子Agent可传入的最大消息条数(防止token爆炸)
MAX_AGENT_HISTORY = 10


def build_agent_prompt_input(state: CustomerServiceState) -> dict:
    """从state构建子Agent的prompt_input — 统一记忆注入逻辑

    解决的问题:
    1. 子Agent的memory_context/conversation_summary原为硬编码空字符串
    2. 子Agent传入全量messages导致token浪费
    3. 统一所有子Agent的记忆注入模式

    Returns:
        可直接传给ChatPromptTemplate.ainvoke()的字典
    """
    from app.memory.profile import UserProfile

    # 构建记忆上下文
    profile_data = state.get("user_profile", {})
    profile = UserProfile(**profile_data) if profile_data else None
    conversation_summary = state.get("conversation_summary", "")
    history_summary = state.get("history_summary", "")

    memory_text = format_memory_for_prompt(
        profile=profile,
        conversation_summary=conversation_summary,
        history_summary=history_summary,
        detailed=False,  # 子Agent使用精简模式: 核心画像+当前摘要，不含跨会话历史
    )

    # 限制消息条数，避免全量传入
    messages = state.get("messages", [])
    recent_messages = messages[-MAX_AGENT_HISTORY:]

    return {
        "user_id": state.get("user_id", ""),
        "session_id": state.get("session_id", ""),
        "memory_context": memory_text,
        "conversation_summary": conversation_summary,
        "history": recent_messages,
    }


async def load_memory_context(store: BaseStore, user_id: str) -> dict:
    """加载记忆上下文(在新会话开始时调用)

    Returns:
        包含 user_profile 和 history_summary 的字典
    """
    profile = await load_user_profile(store, user_id)
    history_summary = await load_recent_summaries(store, user_id, limit=3)

    return {
        "user_profile": profile,
        "history_summary": history_summary,
    }


def format_memory_for_prompt(
    profile: Optional[UserProfile],
    conversation_summary: str = "",
    history_summary: str = "",
    detailed: bool = True,
) -> str:
    """将记忆格式化为可注入System Prompt的文本块

    Args:
        profile: 用户画像
        conversation_summary: 当前会话的对话摘要
        history_summary: 历史会话摘要
        detailed: True=完整画像，False=仅核心信息(子Agent使用以节省token)

    Returns:
        格式化后的记忆文本，直接拼接到System Prompt末尾
    """
    parts = []

    # 用户画像
    if profile:
        profile_text = profile.to_prompt_text(detailed=detailed)
        if profile_text:
            parts.append(f"【用户画像】\n{profile_text}")

    # 当前会话摘要
    if conversation_summary:
        parts.append(f"【当前对话摘要】\n{conversation_summary}")

    # 历史会话摘要(仅detailed模式注入，子Agent通常不需要跨会话历史)
    if detailed and history_summary:
        parts.append(f"【近期历史】\n{history_summary}")

    if not parts:
        return ""

    return "\n\n--- 记忆上下文 ---\n" + "\n\n".join(parts) + "\n--- 记忆上下文结束 ---\n"


async def save_memory_after_response(
    store: BaseStore,
    state: CustomerServiceState,
) -> None:
    """在response节点后保存记忆

    做两件事:
    1. 更新用户画像(交互统计、关键事实)
    2. 保存会话摘要(如果有的话)
    """
    user_id = state.get("user_id", "anonymous")
    intent = state.get("intent")
    sentiment = state.get("sentiment")
    messages = state.get("messages", [])
    needs_escalation = state.get("needs_escalation", False)

    # 更新用户画像(内部处理needs_escalation，避免二次read-modify-write)
    await update_profile_from_state(
        store=store,
        user_id=user_id,
        intent=intent,
        sentiment=sentiment,
        messages=messages,
        needs_escalation=needs_escalation,
    )

    logger.debug("记忆保存完成: user_id=%s, intent=%s", user_id, intent)
