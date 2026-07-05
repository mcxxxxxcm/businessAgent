"""意图分类路由节点 - Agent的"大脑"，将用户消息分类到对应处理分支"""

import logging
from typing import Literal, Optional

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from app.agent.state import CustomerServiceState
from app.agent.prompts import INTENT_ROUTER_PROMPT

logger = logging.getLogger(__name__)


class IntentClassification(BaseModel):
    """意图分类结构化输出"""

    intent: Literal[
        "order_query",
        "product_search",
        "refund_service",
        "knowledge_faq",
        "human_escalation",
        "general_chat",
    ] = Field(description="用户意图分类")

    sentiment: Literal["positive", "neutral", "negative", "angry"] = Field(
        default="neutral", description="用户情感分类"
    )

    sentiment_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="情感强度分数，0.0-1.0，angry时通常>0.8",
    )


async def intent_router_node(state: CustomerServiceState) -> dict:
    """意图分类路由节点

    使用LLM结构化输出对用户消息进行意图分类和情感分析，
    结果用于后续的条件路由决策。
    """
    from app.api.deps import get_llm

    llm = get_llm()

    # 使用结构化输出确保分类结果可靠
    structured_llm = llm.with_structured_output(IntentClassification)

    # 只取最近5条消息控制上下文长度和成本
    recent_messages = state["messages"][-5:]

    try:
        result = await structured_llm.ainvoke(
            [
                SystemMessage(content=INTENT_ROUTER_PROMPT),
                *recent_messages,
            ]
        )
    except Exception as e:
        logger.warning("意图分类失败，降级为general_chat: %s", e)
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

    return {
        "intent": result.intent,
        "sentiment": result.sentiment,
        "sentiment_score": result.sentiment_score,
        "needs_escalation": needs_escalation,
        "escalation_reason": escalation_reason,
        "turn_count": state.get("turn_count", 0) + 1,
    }
