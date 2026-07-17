"""商品搜索Agent节点"""

import logging

from langchain_core.messages import AIMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import PRODUCT_AGENT_PROMPT
from app.tools.product_search import search_products, check_inventory

logger = logging.getLogger(__name__)


async def product_agent_node(state: CustomerServiceState) -> dict:
    """商品搜索Agent - 绑定search_products和check_inventory工具"""
    from app.api.deps import get_llm, llm_semaphore
    from app.memory.manager import build_agent_prompt_input

    llm = get_llm()

    chain = (
        PRODUCT_AGENT_PROMPT
        | llm.bind_tools([search_products, check_inventory])
    )

    prompt_input = build_agent_prompt_input(state)

    try:
        async with llm_semaphore:
            response = await chain.ainvoke(prompt_input)
    except Exception as e:
        logger.error("product_agent LLM调用失败: %s", e)
        response = AIMessage(content="抱歉，搜索商品时遇到了问题，请稍后重试或联系人工客服。")

    return {
        "messages": [response],
        "active_agent": "product_agent",
        "react_step_count": state.get("react_step_count", 0) + 1,
    }
