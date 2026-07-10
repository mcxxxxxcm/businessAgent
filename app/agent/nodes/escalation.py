"""转人工客服节点"""

from app.agent.state import CustomerServiceState
from app.agent.prompts import ESCALATION_PROMPT
from app.tools.human_escalation import transfer_to_human
from app.tools.phone_call import place_phone_call
from app.tools.sms import send_custom_sms


async def escalation_node(state: CustomerServiceState) -> dict:
    """转人工客服节点 - 绑定转人工、电话外呼、短信通知工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()

    chain = (
        ESCALATION_PROMPT
        | llm.bind_tools([transfer_to_human, place_phone_call, send_custom_sms])
    )

    prompt_input = {
        "user_id": state.get("user_id", ""),
        "session_id": state.get("session_id", ""),
        "escalation_reason": state.get("escalation_reason", "用户请求转人工客服"),
        "history": state["messages"],
    }

    async with llm_semaphore:
        response = await chain.ainvoke(prompt_input)

    return {"messages": [response], "active_agent": "escalation"}
