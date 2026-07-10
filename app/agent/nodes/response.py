"""最终响应生成节点 - 生成客服回复并保存记忆

核心职责:
1. 如果子Agent已生成回复(包含工具调用结果)，则直接返回
2. 否则使用LLM生成通用回复(注入记忆上下文)
3. 提取回复元数据(AgentResponseMeta结构化输出)
4. 保存用户长期记忆(画像、交互统计)

结构化输出: 使用AgentResponseMeta对最终回复进行元数据提取，
确保每次回复都有response_type、confidence、suggested_actions等字段，
便于后续分析和监控。
"""

import logging

from langgraph.store.base import BaseStore

from app.agent.state import CustomerServiceState
from app.agent.prompts import RESPONSE_PROMPT
from app.agent.schemas import AgentResponseMeta, structured_llm_output

logger = logging.getLogger(__name__)


async def response_node(state: CustomerServiceState, *, store: BaseStore) -> dict:
    """最终响应节点 - 生成客服回复并保存长期记忆

    此节点作为所有子Agent的汇聚点，负责:
    1. 如果子Agent已生成回复(包含工具调用结果)，则直接返回
    2. 否则使用LLM生成通用回复(注入记忆上下文)
    3. 提取回复元数据(AgentResponseMeta结构化校验)
    4. 保存用户长期记忆(画像、交互统计)
    """
    from app.api.deps import get_llm, llm_semaphore
    from app.memory.manager import format_memory_for_prompt
    from app.memory.profile import UserProfile

    messages = state["messages"]
    last_message = messages[-1] if messages else None
    ai_response = None

    # 如果最后一条已经是AI消息且有内容，直接使用(子Agent已生成回复)
    if last_message and hasattr(last_message, "type") and last_message.type == "ai":
        if last_message.content and not getattr(last_message, "tool_calls", None):
            ai_response = last_message

    # 否则生成通用回复
    if ai_response is None:
        llm = get_llm()

        # 构建记忆上下文
        profile_data = state.get("user_profile", {})
        profile = UserProfile(**profile_data) if profile_data else None
        conversation_summary = state.get("conversation_summary", "")
        history_summary = state.get("history_summary", "")
        memory_text = format_memory_for_prompt(
            profile=profile,
            conversation_summary=conversation_summary,
            history_summary=history_summary,
        )

        # 使用ChatPromptTemplate + LCEL管道
        chain = RESPONSE_PROMPT | llm

        prompt_input = {
            "user_id": state.get("user_id", ""),
            "session_id": state.get("session_id", ""),
            "memory_context": memory_text or "",
            "conversation_summary": conversation_summary or "",
            "history": state["messages"][-10:],  # 最近10条消息
        }

        async with llm_semaphore:
            ai_response = await chain.ainvoke(
                prompt_input,
                config={"tags": ["response"]},  # 标记为response节点输出，SSE只放行此tag
            )

    # === 提取回复元数据(AgentResponseMeta结构化输出) ===
    response_meta = await _extract_response_meta(ai_response, state)

    # 保存长期记忆
    await _save_memory(state, store)

    result = {"messages": [ai_response]}
    if response_meta:
        result["response_meta"] = response_meta

    return result


async def _extract_response_meta(ai_message, state: CustomerServiceState) -> dict | None:
    """从AI回复中提取结构化元数据

    使用AgentResponseMeta模型 + structured_llm_output四层降级链:
    Layer 1: with_structured_output(默认)
    Layer 2: with_structured_output(method="tool_calling")
    Layer 3: bind_tools + 解析tool_calls
    Layer 4: JSON Prompt + 手动解析
    失败时使用规则推断兜底。
    """
    from app.api.deps import get_llm, llm_semaphore
    from app.agent.schemas import AgentResponseMeta, structured_llm_output

    content = ai_message.content if hasattr(ai_message, "content") and ai_message.content else ""
    if not content or len(content) < 5:
        return None

    # 构建元数据提取Prompt
    meta_prompt = f"""请分析以下客服回复，提取元数据。

客服回复内容:
{content[:500]}

用户意图: {state.get('intent', 'unknown')}
用户情感: {state.get('sentiment', 'neutral')}"""

    from langchain_core.messages import SystemMessage, HumanMessage
    meta_messages = [
        SystemMessage(content="你是一个回复分析助手。请严格按照要求输出结构化数据。"),
        HumanMessage(content=meta_prompt),
    ]

    llm = get_llm()
    result = await structured_llm_output(
        model_class=AgentResponseMeta,
        llm=llm,
        messages=meta_messages,
        semaphore=llm_semaphore,
    )

    if result is not None:
        meta_dict = result.model_dump()
        logger.info(
            "回复元数据(结构化): type=%s, confidence=%.2f, followup=%s",
            meta_dict["response_type"],
            meta_dict["confidence"],
            meta_dict["needs_followup"],
        )
        return meta_dict

    # 四层都失败 → 基于规则推断兜底
    return _rule_based_meta(content, state)


def _rule_based_meta(content: str, state: CustomerServiceState) -> dict:
    """基于规则的回复元数据推断(四层降级均失败时的兜底)"""
    intent = state.get("intent", "general_chat")
    sentiment = state.get("sentiment", "neutral")
    needs_escalation = state.get("needs_escalation", False)

    type_mapping = {
        "human_escalation": "escalation_notice",
        "general_chat": "general_chat",
    }
    response_type = type_mapping.get(intent, "direct_answer")

    tool_keywords = ["订单", "物流", "库存", "退款", "搜索结果"]
    if any(kw in content for kw in tool_keywords):
        response_type = "tool_result_summary"

    confidence = 0.5 if (needs_escalation or sentiment in ("negative", "angry")) else 0.8

    result = {
        "response_type": response_type,
        "confidence": confidence,
        "suggested_actions": [],
        "needs_followup": intent in ("refund_service", "order_query"),
    }

    logger.info(
        "回复元数据(规则): type=%s, confidence=%.2f, followup=%s",
        result["response_type"],
        result["confidence"],
        result["needs_followup"],
    )
    return result


async def _save_memory(state: CustomerServiceState, store: BaseStore) -> None:
    """保存记忆: 用户画像更新"""
    from app.memory.manager import save_memory_after_response

    try:
        await save_memory_after_response(store, state)
    except Exception as e:
        logger.warning("保存记忆失败: %s", e)
