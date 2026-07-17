"""订单查询Agent节点"""

import logging

from langchain_core.messages import AIMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import ORDER_AGENT_PROMPT
from app.tools.order_query import query_order, track_logistics
from app.tools.sms import send_order_notification

logger = logging.getLogger(__name__)


async def order_agent_node(state: CustomerServiceState) -> dict:
    """订单查询Agent - 绑定query_order、track_logistics和send_order_notification工具"""
    from app.api.deps import get_llm, llm_semaphore
    from app.memory.manager import build_agent_prompt_input

    llm = get_llm()

    chain = (
        ORDER_AGENT_PROMPT
        | llm.bind_tools([query_order, track_logistics, send_order_notification])
    )

    prompt_input = build_agent_prompt_input(state)

    try:
        async with llm_semaphore:
            response = await chain.ainvoke(prompt_input)
    except Exception as e:
        logger.error("order_agent LLM调用失败: %s", e)
        response = AIMessage(content="抱歉，处理您的订单查询时遇到了问题，请稍后重试或联系人工客服。")

    return {
        "messages": [response],
        "active_agent": "order_agent",
        "react_step_count": state.get("react_step_count", 0) + 1,
    }
