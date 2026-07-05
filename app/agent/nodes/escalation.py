"""转人工客服节点"""

from langchain_core.messages import SystemMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import ESCALATION_PROMPT
from app.tools.human_escalation import transfer_to_human


async def escalation_node(state: CustomerServiceState) -> dict:
    """转人工客服节点 - 绑定transfer_to_human工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()
    escalation_llm = llm.bind_tools([transfer_to_human])

    system_content = ESCALATION_PROMPT.format(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
    )

    # 添加转人工原因到上下文
    escalation_reason = state.get("escalation_reason", "用户请求转人工客服")

    async with llm_semaphore:
        response = await escalation_llm.ainvoke(
            [
                SystemMessage(content=system_content),
                *state["messages"],
                SystemMessage(content=f"转人工原因: {escalation_reason}"),
            ]
        )

    return {"messages": [response]}
