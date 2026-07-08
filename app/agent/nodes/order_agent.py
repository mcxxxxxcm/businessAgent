"""订单查询Agent节点"""

from langchain_core.messages import SystemMessage, HumanMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import ORDER_AGENT_PROMPT
from app.tools.order_query import query_order, track_logistics


async def order_agent_node(state: CustomerServiceState) -> dict:
    """订单查询Agent - 绑定query_order和track_logistics工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()
    order_llm = llm.bind_tools([query_order, track_logistics])

    # 格式化system prompt
    system_content = ORDER_AGENT_PROMPT.format(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
    )

    async with llm_semaphore:
        response = await order_llm.ainvoke(
            [
                SystemMessage(content=system_content),
                *state["messages"],
            ]
        )

    return {"messages": [response], "active_agent": "order_agent"}
