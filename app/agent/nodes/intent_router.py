"""意图分类路由节点 - Agent的"大脑"，将用户消息分类到对应处理分支

核心逻辑:
1. 先用 MultiIntentDecomposition 拆解意图(支持单意图和多意图)
2. 如果只有1个意图 → 走原 IntentClassification 单意图路径(零开销)
3. 如果有多个意图 → 写入 sub_intents，路由到 task_orchestrator 编排执行

使用统一的结构化输出函数 structured_llm_output，四层降级链。
"""

import logging

from langgraph.store.base import BaseStore

from app.agent.state import CustomerServiceState
from app.agent.prompts import INTENT_ROUTER_PROMPT, MULTI_INTENT_PROMPT
from app.agent.schemas import (
    IntentClassification,
    MultiIntentDecomposition,
    structured_llm_output,
)

logger = logging.getLogger(__name__)

# 多意图拆解置信度阈值 — 低于此值当作单意图处理(防误拆)
MULTI_INTENT_CONFIDENCE_THRESHOLD = 0.6


async def intent_router_node(state: CustomerServiceState, *, store: BaseStore) -> dict:
    """意图分类路由节点

    同时完成:
    1. 入口预检: 消息数超阈值时先压缩(防止长对话token爆炸)
    2. LLM意图拆解(先尝试多意图，单意图时降级为原路径)
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

    prompt_input = {
        "memory_context": memory_text or "",
        "history": recent_messages,
    }

    # === 第一步: 多意图拆解 ===
    multi_result = await structured_llm_output(
        model_class=MultiIntentDecomposition,
        llm=llm,
        prompt_template=MULTI_INTENT_PROMPT,
        prompt_input=prompt_input,
        config={"tags": ["internal"]},
        semaphore=llm_semaphore,
    )

    # 将画像数据存入state，供后续节点使用
    profile_dict = profile.model_dump() if profile else {}
    base_updates = {
        "turn_count": state.get("turn_count", 0) + 1,
        "user_profile": profile_dict,
        "history_summary": history_summary,
        **summary_updates,
    }

    # === 判断: 单意图走原路径，多意图走编排路径 ===
    if multi_result is not None and len(multi_result.intents) >= 1:
        if len(multi_result.intents) == 1 or multi_result.confidence < MULTI_INTENT_CONFIDENCE_THRESHOLD:
            # 单意图 或 拆解置信度低 → 走原 IntentClassification 路径
            sub = multi_result.intents[0]
            # 映射 tool_hint → intent (tool_hint就是intent值)
            intent_value = sub.tool_hint
            # 单意图时也需要情感分析，用原IntentClassification
            single_result = await structured_llm_output(
                model_class=IntentClassification,
                llm=llm,
                prompt_template=INTENT_ROUTER_PROMPT,
                prompt_input=prompt_input,
                config={"tags": ["internal"]},
                semaphore=llm_semaphore,
            )
            if single_result is None:
                single_result = IntentClassification(
                    intent=intent_value,
                    sentiment="neutral",
                    sentiment_score=0.0,
                )

            needs_escalation = single_result.sentiment == "angry" and single_result.sentiment_score > 0.8
            escalation_reason = None
            if needs_escalation:
                escalation_reason = f"用户情感愤怒(score={single_result.sentiment_score})，自动转人工"

            logger.info(
                "单意图路由: intent=%s, sentiment=%s, score=%.2f",
                single_result.intent, single_result.sentiment, single_result.sentiment_score,
            )
            return {
                "intent": single_result.intent,
                "sentiment": single_result.sentiment,
                "sentiment_score": single_result.sentiment_score,
                "needs_escalation": needs_escalation,
                "escalation_reason": escalation_reason,
                "sub_intents": [],      # 空=单意图
                "current_sub_idx": 0,
                "sub_results": [],
                **base_updates,
            }
        else:
            # 多意图 → 写入sub_intents，路由到task_orchestrator
            sub_intents_data = [si.model_dump() for si in multi_result.intents]
            logger.info(
                "多意图拆解: %d个子意图, confidence=%.2f, reasoning=%s",
                len(multi_result.intents), multi_result.confidence, multi_result.reasoning[:100],
            )

            # 情感分析: 用第一个子意图的tool_hint做简单分类
            # 多意图场景下情感通常不极端(极端情感走转人工)
            return {
                "intent": multi_result.intents[0].tool_hint,  # 主意图
                "sentiment": "neutral",
                "sentiment_score": 0.0,
                "needs_escalation": False,
                "escalation_reason": None,
                "sub_intents": sub_intents_data,
                "current_sub_idx": 0,
                "sub_results": [],
                **base_updates,
            }

    # === 降级: 多意图拆解失败 → 回退到原 IntentClassification ===
    logger.warning("多意图拆解失败，回退到单意图分类")
    result = await structured_llm_output(
        model_class=IntentClassification,
        llm=llm,
        prompt_template=INTENT_ROUTER_PROMPT,
        prompt_input=prompt_input,
        config={"tags": ["internal"]},
        semaphore=llm_semaphore,
    )

    if result is None:
        logger.warning("意图分类失败(所有4层降级均失败)，降级为general_chat")
        result = IntentClassification(
            intent="general_chat",
            sentiment="neutral",
            sentiment_score=0.0,
        )

    needs_escalation = result.sentiment == "angry" and result.sentiment_score > 0.8
    escalation_reason = None
    if needs_escalation:
        escalation_reason = f"用户情感愤怒(score={result.sentiment_score})，自动转人工"

    logger.info(
        "意图分类结果(降级): intent=%s, sentiment=%s, score=%.2f",
        result.intent, result.sentiment, result.sentiment_score,
    )

    return {
        "intent": result.intent,
        "sentiment": result.sentiment,
        "sentiment_score": result.sentiment_score,
        "needs_escalation": needs_escalation,
        "escalation_reason": escalation_reason,
        "sub_intents": [],
        "current_sub_idx": 0,
        "sub_results": [],
        **base_updates,
    }
