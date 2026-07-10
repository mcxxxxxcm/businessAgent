"""订单查询Agent节点"""

from app.agent.state import CustomerServiceState
from app.agent.prompts import ORDER_AGENT_PROMPT
from app.tools.order_query import query_order, track_logistics
from app.tools.sms import send_order_notification


async def order_agent_node(state: CustomerServiceState) -> dict:
    """订单查询Agent - 绑定query_order、track_logistics和send_order_notification工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()

    # 使用ChatPromptTemplate + LCEL管道 + bind_tools
    chain = (
        ORDER_AGENT_PROMPT
        | llm.bind_tools([query_order, track_logistics, send_order_notification])
    )

    # 构建输入变量
    prompt_input = {
        "user_id": state.get("user_id", ""),
        "session_id": state.get("session_id", ""),
        "memory_context": "",
        "conversation_summary": "",
        "history": state["messages"],
    }

    async with llm_semaphore:
        response = await chain.ainvoke(prompt_input)

    return {"messages": [response], "active_agent": "order_agent"}
