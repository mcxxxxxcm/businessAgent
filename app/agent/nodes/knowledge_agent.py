"""知识库RAG Agent节点"""

import logging

from langchain_core.messages import AIMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import KNOWLEDGE_AGENT_PROMPT
from app.tools.knowledge_rag import search_knowledge_base

logger = logging.getLogger(__name__)


async def knowledge_agent_node(state: CustomerServiceState) -> dict:
    """知识库RAG Agent - 绑定search_knowledge_base工具"""
    from app.api.deps import get_llm, acquire_llm_semaphore, release_llm_semaphore, LLMQueueTimeoutError
    from app.memory.manager import build_agent_prompt_input

    llm = get_llm()

    chain = (
        KNOWLEDGE_AGENT_PROMPT
        | llm.bind_tools([search_knowledge_base])
    )

    prompt_input = build_agent_prompt_input(state)

    try:
        await acquire_llm_semaphore()
        try:
            response = await chain.ainvoke(prompt_input)
        finally:
            release_llm_semaphore()
    except LLMQueueTimeoutError:
        logger.warning("knowledge_agent LLM排队超时，返回503")
        response = AIMessage(content="当前服务繁忙，请稍后重试或联系人工客服。")
    except Exception as e:
        logger.error("knowledge_agent LLM调用失败: %s", e)
        response = AIMessage(content="抱歉，检索知识库时遇到了问题，请稍后重试或联系人工客服。")

    return {
        "messages": [response],
        "active_agent": "knowledge_agent",
        "react_step_count": state.get("react_step_count", 0) + 1,
    }
