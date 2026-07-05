"""依赖注入 - LLM实例、Graph实例等"""

import asyncio
from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import settings

# LLM并发控制信号量
llm_semaphore = asyncio.Semaphore(5)

# 图实例缓存
_graph_instance = None


@lru_cache
def get_llm() -> ChatOpenAI:
    """获取智谱GLM-4 LLM实例(通过OpenAI兼容模式)

    使用ChatOpenAI + openai_api_base指向智谱API端点，
    可获得最完整的LangChain生态兼容(流式/结构化输出/工具绑定)。
    """
    return ChatOpenAI(
        model=settings.ZHIPU_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
        openai_api_key=settings.ZHIPU_API_KEY,
        openai_api_base=settings.ZHIPU_API_BASE,
        streaming=True,
    )


async def get_graph():
    """获取编译好的LangGraph图实例(单例)"""
    global _graph_instance
    if _graph_instance is not None:
        return _graph_instance

    from app.agent.graph import compile_graph
    from app.memory.checkpointer import get_checkpointer
    from app.memory.store import get_store

    checkpointer = await get_checkpointer()
    store = await get_store()
    _graph_instance = await compile_graph(checkpointer, store)
    return _graph_instance
