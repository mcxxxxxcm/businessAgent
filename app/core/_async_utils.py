"""异步单例初始化工具 - 解决 TOCTOU 竞态条件

问题: 多个协程同时调用 get_X() 时，if X is None 检查
     和 await init() 赋值之间存在竞态窗口。

解决: 使用 per-key Lock + 双重检查锁定模式:
     1. 无锁快速路径: if X is not None → return X (99.9% 走此路径)
     2. 加锁慢路径: acquire per-key lock → double-check → init → release

per-key Lock 避免嵌套初始化死锁:
    get_checkpointer() 持有 "_checkpointer" 锁后，内部调用
    get_pg_pool() 只需获取 "_pool" 锁，不会阻塞。

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

# 每 key 一把锁，避免嵌套初始化死锁
# (全局单锁模式下，get_checkpointer 持锁后内部调用 get_pg_pool
#  会再次请求同一把锁，导致同协程死锁)
_key_locks: dict[str, asyncio.Lock] = {}


def _get_key_lock(key: str) -> asyncio.Lock:
    """获取 per-key 初始化锁(懒创建，绑定到当前事件循环)"""
    if key not in _key_locks:
        _key_locks[key] = asyncio.Lock()
    return _key_locks[key]


async def async_init_singleton(
    holder: dict,
    key: str,
    factory: Callable[[], Awaitable[T]],
) -> T:
    """协程安全的异步单例初始化

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

    # 慢路径: 加 per-key 锁初始化
    async with _get_key_lock(key):
        # 双重检查: 可能其他协程已经完成了初始化
        instance = holder.get(key)
        if instance is not None:
            return instance

        # 执行初始化
        instance = await factory()
        holder[key] = instance
        logger.debug("单例初始化完成: %s", key)
        return instance
