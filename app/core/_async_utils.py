"""异步单例初始化工具 - 解决 TOCTOU 竞态条件

问题: 多个协程同时调用 get_X() 时，if X is None 检查
     和 await init() 赋值之间存在竞态窗口。

解决: 使用 asyncio.Lock + 双重检查锁定模式:
     1. 无锁快速路径: if X is not None → return X (99.9% 走此路径)
     2. 加锁慢路径: acquire lock → double-check → init → release

用法:
    _pool: AsyncConnectionPool | None = None

    async def _create_pool() -> AsyncConnectionPool:
        pool = AsyncConnectionPool(...)
        await pool.open()
        return pool

    async def get_pg_pool() -> AsyncConnectionPool:
        return await async_init_singleton(globals(), "_pool", _create_pool)
"""

import asyncio
import logging
from typing import Callable, Awaitable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 全局初始化锁 - 单个锁足够，初始化是一次性操作
_init_lock = asyncio.Lock()


async def async_init_singleton(
    holder: dict,
    key: str,
    factory: Callable[[], Awaitable[T]],
) -> T:
    """线程安全的异步单例初始化

    Args:
        holder: 存放单例的字典 (如 globals())
        key: 单例在 holder 中的键名 (如 "_pool")
        factory: 异步工厂函数，返回单例实例

    Returns:
        单例实例
    """
    # 快速路径: 无锁检查 (热路径，每次请求都会走)
    instance = holder.get(key)
    if instance is not None:
        return instance

    # 慢路径: 加锁初始化
    async with _init_lock:
        # 双重检查: 可能其他协程已经完成了初始化
        instance = holder.get(key)
        if instance is not None:
            return instance

        # 执行初始化
        instance = await factory()
        holder[key] = instance
        logger.debug("单例初始化完成: %s", key)
        return instance
