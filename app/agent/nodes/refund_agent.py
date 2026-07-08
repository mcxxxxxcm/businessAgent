"""售后退款Agent节点"""

from langchain_core.messages import SystemMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import REFUND_AGENT_PROMPT
from app.tools.refund import create_refund, create_service_ticket, query_refund_status


async def refund_agent_node(state: CustomerServiceState) -> dict:
    """售后退款Agent - 绑定create_refund、create_service_ticket和query_refund_status工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()
    refund_llm = llm.bind_tools([create_refund, create_service_ticket, query_refund_status])

    system_content = REFUND_AGENT_PROMPT.format(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
    )

    async with llm_semaphore:
        response = await refund_llm.ainvoke(
            [
                SystemMessage(content=system_content),
                *state["messages"],
            ]
        )

    return {"messages": [response], "active_agent": "refund_agent"}
