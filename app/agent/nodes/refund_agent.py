"""售后退款Agent节点"""

from app.agent.state import CustomerServiceState
from app.agent.prompts import REFUND_AGENT_PROMPT
from app.tools.refund import create_refund, create_service_ticket, query_refund_status
from app.tools.sms import send_refund_notification


async def refund_agent_node(state: CustomerServiceState) -> dict:
    """售后退款Agent - 绑定退款工具和退款通知短信工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()

    chain = (
        REFUND_AGENT_PROMPT
        | llm.bind_tools([create_refund, create_service_ticket, query_refund_status, send_refund_notification])
    )

    prompt_input = {
        "user_id": state.get("user_id", ""),
        "session_id": state.get("session_id", ""),
        "memory_context": "",
        "conversation_summary": "",
        "history": state["messages"],
    }

    async with llm_semaphore:
        response = await chain.ainvoke(prompt_input)

    return {"messages": [response], "active_agent": "refund_agent"}
