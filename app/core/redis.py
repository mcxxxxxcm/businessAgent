"""Redis连接池管理"""

import redis.asyncio as redis
from app.core.config import settings

_redis_pool: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """获取Redis连接池实例(单例)"""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=50,
        )
    return _redis_pool


async def close_redis() -> None:
    """关闭Redis连接池"""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None
