"""依赖注入 - LLM实例、Graph实例等"""

import asyncio
import logging
from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core._async_utils import async_init_singleton

logger = logging.getLogger(__name__)


class LLMQueueTimeoutError(Exception):
    """LLM信号量排队超时 — 请求过多时快速失败而非无限等待"""
    pass


# LLM并发控制信号量(大小由配置决定)
llm_semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)


async def acquire_llm_semaphore():
    """获取LLM信号量(带超时) — 防止请求无限排队导致OOM

    超时后抛出LLMQueueTimeoutError，由调用方处理(返回503或降级回复)。
    """
    try:
        await asyncio.wait_for(
            llm_semaphore.acquire(),
            timeout=settings.LLM_QUEUE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "LLM信号量排队超时(%.0fs)，当前并发=%d，可能需要扩容",
            settings.LLM_QUEUE_TIMEOUT,
            settings.LLM_MAX_CONCURRENT,
        )
        raise LLMQueueTimeoutError(
            f"LLM排队超时({settings.LLM_QUEUE_TIMEOUT}s)，请稍后重试"
        )


def release_llm_semaphore():
    """释放LLM信号量"""
    llm_semaphore.release()


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


# 图实例缓存
_graph_instance = None


async def _create_graph():
    """创建并编译LangGraph图实例"""
    from app.agent.graph import compile_graph
    from app.memory.checkpointer import get_checkpointer
    from app.memory.store import get_store

    checkpointer = await get_checkpointer()
    store = await get_store()
    return await compile_graph(checkpointer, store)


async def get_graph():
    """获取编译好的LangGraph图实例(单例，竞态安全)"""
    return await async_init_singleton(globals(), "_graph_instance", _create_graph)


# 外呼管理器缓存
_outbound_manager = None


async def _create_outbound_manager():
    """创建并初始化外呼管理器"""
    from app.voice.outbound import OutboundManager

    manager = OutboundManager(
        freeswitch_host="127.0.0.1",
        freeswitch_esl_port=8021,
        freeswitch_esl_password="ClueCon",
        gateway_ws_url="ws://127.0.0.1:8765",
    )

    # 尝试连接FreeSWITCH ESL (连接失败也不影响启动)
    try:
        await manager.connect_esl()
    except Exception as e:
        logger.warning("FreeSWITCH ESL连接失败，降级为模拟模式: %s", e)

    return manager


async def get_outbound_manager():
    """获取外呼管理器(单例，竞态安全)"""
    return await async_init_singleton(globals(), "_outbound_manager", _create_outbound_manager)
