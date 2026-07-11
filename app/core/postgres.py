"""PostgreSQL连接池管理"""

import logging

from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from app.core.config import settings
from app.core._async_utils import async_init_singleton

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


async def _create_pool() -> AsyncConnectionPool:
    """创建并打开PG连接池"""
    pool = AsyncConnectionPool(
        conninfo=settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        max_idle=300.0,
        max_lifetime=3600.0,
        open=False,  # 不在构造函数中open，避免RuntimeWarning
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
        },
    )
    await pool.open()
    logger.info("PG连接池已创建 (min=%d, max=%d)", pool.min_size, pool.max_size)
    return pool


async def get_pg_pool() -> AsyncConnectionPool:
    """获取PostgreSQL异步连接池(单例，竞态安全)"""
    return await async_init_singleton(globals(), "_pool", _create_pool)


async def close_pg_pool() -> None:
    """关闭PostgreSQL连接池"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PG连接池已关闭")
