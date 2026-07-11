"""Redis连接池管理"""

import redis.asyncio as redis
from app.core.config import settings
from app.core._async_utils import async_init_singleton

_redis_pool: redis.Redis | None = None


async def _create_redis() -> redis.Redis:
    """创建Redis连接"""
    return redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        max_connections=50,
    )


async def get_redis() -> redis.Redis:
    """获取Redis连接池实例(单例，竞态安全)"""
    return await async_init_singleton(globals(), "_redis_pool", _create_redis)


async def close_redis() -> None:
    """关闭Redis连接池"""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None
