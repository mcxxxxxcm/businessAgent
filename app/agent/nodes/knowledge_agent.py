"""知识库RAG Agent节点"""

from langchain_core.messages import SystemMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import KNOWLEDGE_AGENT_PROMPT
from app.tools.knowledge_rag import search_knowledge_base


async def knowledge_agent_node(state: CustomerServiceState) -> dict:
    """知识库RAG Agent - 绑定search_knowledge_base工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()
    knowledge_llm = llm.bind_tools([search_knowledge_base])

    system_content = KNOWLEDGE_AGENT_PROMPT.format(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
    )

    async with llm_semaphore:
        response = await knowledge_llm.ainvoke(
            [
                SystemMessage(content=system_content),
                *state["messages"],
            ]
        )

    return {"messages": [response]}
