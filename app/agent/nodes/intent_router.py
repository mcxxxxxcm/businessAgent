"""意图分类路由节点 - Agent的"大脑"，将用户消息分类到对应处理分支

同时负责加载用户画像和记忆上下文，注入到后续节点的Prompt中。
使用统一的结构化输出函数 structured_llm_output，四层降级链:
Layer 1: with_structured_output(默认) → Layer 2: tool_calling → Layer 3: bind_tools → Layer 4: JSON解析
"""

import logging

from langgraph.store.base import BaseStore

from app.agent.state import CustomerServiceState
from app.agent.prompts import INTENT_ROUTER_PROMPT
from app.agent.schemas import IntentClassification, structured_llm_output

logger = logging.getLogger(__name__)


async def intent_router_node(state: CustomerServiceState, *, store: BaseStore) -> dict:
    """意图分类路由节点

    同时完成:
    1. LLM意图分类+情感分析（四层降级结构化输出）
    2. 加载用户画像和历史摘要(记忆上下文)
    """
    from app.api.deps import get_llm, llm_semaphore
    from app.memory.profile import load_user_profile, load_recent_summaries
    from app.memory.manager import format_memory_for_prompt

    llm = get_llm()

    # === 加载记忆上下文 ===
    user_id = state.get("user_id", "anonymous")
    try:
        profile = await load_user_profile(store, user_id)
        history_summary = await load_recent_summaries(store, user_id, limit=3)
    except Exception as e:
        logger.warning("加载记忆上下文失败: %s", e)
        profile = None
        history_summary = ""

    # 格式化记忆文本
    conversation_summary = state.get("conversation_summary", "")
    memory_text = format_memory_for_prompt(
        profile=profile,
        conversation_summary=conversation_summary,
        history_summary=history_summary,
    )

    # 只取最近5条消息控制上下文长度和成本
    recent_messages = state["messages"][-5:]

    # === 使用统一结构化输出函数(四层降级链) ===
    prompt_input = {
        "memory_context": memory_text or "",
        "history": recent_messages,
    }

    result = await structured_llm_output(
        model_class=IntentClassification,
        llm=llm,
        prompt_template=INTENT_ROUTER_PROMPT,
        prompt_input=prompt_input,
        config={"tags": ["internal"]},
        semaphore=llm_semaphore,
    )

    # 所有层都失败，使用默认值
    if result is None:
        logger.warning("意图分类失败(所有4层降级均失败)，降级为general_chat")
        result = IntentClassification(
            intent="general_chat",
            sentiment="neutral",
            sentiment_score=0.0,
        )

    # 愤怒情感自动触发转人工
    needs_escalation = result.sentiment == "angry" and result.sentiment_score > 0.8
    escalation_reason = None
    if needs_escalation:
        escalation_reason = f"用户情感愤怒(score={result.sentiment_score})，自动转人工"

    logger.info(
        "意图分类结果: intent=%s, sentiment=%s, score=%.2f, escalation=%s",
        result.intent,
        result.sentiment,
        result.sentiment_score,
        needs_escalation,
    )

    # 将画像数据存入state，供后续节点使用
    profile_dict = profile.model_dump() if profile else {}

    return {
        "intent": result.intent,
        "sentiment": result.sentiment,
        "sentiment_score": result.sentiment_score,
        "needs_escalation": needs_escalation,
        "escalation_reason": escalation_reason,
        "turn_count": state.get("turn_count", 0) + 1,
        "user_profile": profile_dict,
        "history_summary": history_summary,
    }
