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
    1. 入口预检: 消息数超阈值时先压缩(防止长对话token爆炸)
    2. LLM意图分类+情感分析（四层降级结构化输出）
    3. 加载用户画像和历史摘要(记忆上下文)
    """
    from app.api.deps import get_llm, llm_semaphore
    from app.memory.profile import load_user_profile, load_recent_summaries
    from app.memory.manager import format_memory_for_prompt

    llm = get_llm()

    # === 入口预检: 长对话消息压缩 ===
    summary_updates = {}
    messages = state.get("messages", [])
    if len(messages) > 25:  # 比response后的阈值(20)更宽松，兜底保护
        logger.info("入口预检: 消息数=%d > 25，触发紧急摘要压缩", len(messages))
        from app.memory.summarizer import summarize_conversation
        summary_updates = await summarize_conversation(state, store=store)
        # 更新messages供后续使用
        if "messages" in summary_updates:
            messages = state["messages"]  # RemoveMessage由graph reducer处理
        if "conversation_summary" in summary_updates:
            state = {**state, "conversation_summary": summary_updates["conversation_summary"]}

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

    # 动态窗口: 短对话全取，长对话取摘要+最近8条
    if len(messages) <= 8:
        recent_messages = messages
    else:
        recent_messages = messages[-8:]

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

    # 合并入口预检的摘要更新
    return {
        "intent": result.intent,
        "sentiment": result.sentiment,
        "sentiment_score": result.sentiment_score,
        "needs_escalation": needs_escalation,
        "escalation_reason": escalation_reason,
        "turn_count": state.get("turn_count", 0) + 1,
        "user_profile": profile_dict,
        "history_summary": history_summary,
        **summary_updates,  # 入口预检的摘要结果(RemoveMessage + conversation_summary)
    }
