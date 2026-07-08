"""商品搜索Agent节点"""

from langchain_core.messages import SystemMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import PRODUCT_AGENT_PROMPT
from app.tools.product_search import search_products, check_inventory


async def product_agent_node(state: CustomerServiceState) -> dict:
    """商品搜索Agent - 绑定search_products和check_inventory工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()
    product_llm = llm.bind_tools([search_products, check_inventory])

    system_content = PRODUCT_AGENT_PROMPT.format(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
    )

    async with llm_semaphore:
        response = await product_llm.ainvoke(
            [
                SystemMessage(content=system_content),
                *state["messages"],
            ]
        )

    return {"messages": [response], "active_agent": "product_agent"}
