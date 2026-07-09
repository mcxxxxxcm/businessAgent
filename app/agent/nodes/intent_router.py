"""意图分类路由节点 - Agent的"大脑"，将用户消息分类到对应处理分支

同时负责加载用户画像和记忆上下文，注入到后续节点的Prompt中。
"""

import json
import logging
from typing import Literal, Optional

from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field
from langgraph.store.base import BaseStore

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

    sentiment: Literal["positive", "neutral", "negative", "angry"] = (
        Field(default="neutral", description="用户情感分类")
    )

    sentiment_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="情感强度分数，0.0-1.0，angry时通常>0.8",
    )


# 要求LLM输出纯JSON的附加指令
_JSON_INSTRUCTION = """

请严格按照以下JSON格式输出分类结果，不要输出任何其他内容（不要markdown标记、不要解释）：

{"intent": "分类值", "sentiment": "情感值", "sentiment_score": 分数值}

分类值必须是: order_query, product_search, refund_service, knowledge_faq, human_escalation, general_chat 之一
情感值必须是: positive, neutral, negative, angry 之一
分数值必须是: 0.0到1.0之间的数字"""


def _parse_json_response(text: str) -> IntentClassification:
    """从LLM回复中提取JSON并解析为IntentClassification

    兼容各种格式：
    - 纯JSON
    - ```json ... ``` 包裹的JSON
    - JSON前后有额外文字
    """
    import re

    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if json_match:
        text = json_match.group(1).strip()

    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        text = brace_match.group(0)

    try:
        data = json.loads(text)
        return IntentClassification(**data)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("JSON解析失败: %s, 原始文本: %s", e, text[:200])
        raise ValueError(f"无法解析意图分类结果: {e}") from e


async def intent_router_node(state: CustomerServiceState, *, store: BaseStore) -> dict:
    """意图分类路由节点

    同时完成:
    1. LLM意图分类+情感分析
    2. 加载用户画像和历史摘要(记忆上下文)
    """
    from app.api.deps import get_llm
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

    # 构建带记忆上下文的System Prompt
    system_prompt = INTENT_ROUTER_PROMPT + _JSON_INSTRUCTION
    if memory_text:
        system_prompt += "\n\n以下是该用户的历史信息，辅助你进行意图分类:\n" + memory_text

    # 只取最近5条消息控制上下文长度和成本
    recent_messages = state["messages"][-5:]

    try:
        # 直接用普通LLM调用，要求返回JSON
        # tags=["internal"] 标记为内部调用，SSE流不过滤给前端
        response = await llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                *recent_messages,
            ],
            config={"tags": ["internal"]},
        )

        result = _parse_json_response(response.content)

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

    # 将画像数据存入state，供后续节点使用
    profile_dict = profile.to_dict() if profile else {}

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
