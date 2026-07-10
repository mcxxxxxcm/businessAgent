"""知识库RAG Agent节点"""

from app.agent.state import CustomerServiceState
from app.agent.prompts import KNOWLEDGE_AGENT_PROMPT
from app.tools.knowledge_rag import search_knowledge_base


async def knowledge_agent_node(state: CustomerServiceState) -> dict:
    """知识库RAG Agent - 绑定search_knowledge_base工具"""
    from app.api.deps import get_llm, llm_semaphore

    llm = get_llm()

    chain = (
        KNOWLEDGE_AGENT_PROMPT
        | llm.bind_tools([search_knowledge_base])
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

    return {"messages": [response], "active_agent": "knowledge_agent"}
