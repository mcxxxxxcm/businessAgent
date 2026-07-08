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
    save_user_profile,
    update_profile_from_state,
    load_recent_summaries,
)
from app.memory.summarizer import should_summarize, summarize_conversation

logger = logging.getLogger(__name__)


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
) -> str:
    """将记忆格式化为可注入System Prompt的文本块

    Args:
        profile: 用户画像
        conversation_summary: 当前会话的对话摘要
        history_summary: 历史会话摘要

    Returns:
        格式化后的记忆文本，直接拼接到System Prompt末尾
    """
    parts = []

    # 用户画像
    if profile:
        profile_text = profile.to_prompt_text()
        if profile_text:
            parts.append(f"【用户画像】\n{profile_text}")

    # 当前会话摘要
    if conversation_summary:
        parts.append(f"【当前对话摘要】\n{conversation_summary}")

    # 历史会话摘要
    if history_summary:
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

    # 更新用户画像
    await update_profile_from_state(
        store=store,
        user_id=user_id,
        intent=intent,
        sentiment=sentiment,
        messages=messages,
    )

    # 如果有转人工，更新转人工计数
    if needs_escalation:
        profile = await load_user_profile(store, user_id)
        profile.escalation_count += 1
        await save_user_profile(store, user_id, profile)

    logger.debug("记忆保存完成: user_id=%s, intent=%s", user_id, intent)
