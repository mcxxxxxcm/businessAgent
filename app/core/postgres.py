"""PostgreSQL连接池管理"""

import logging

from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


async def get_pg_pool() -> AsyncConnectionPool:
    """获取PostgreSQL异步连接池(单例)"""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
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
        await _pool.open()
        logger.info("PG连接池已创建 (min=%d, max=%d)", _pool.min_size, _pool.max_size)
    return _pool


async def close_pg_pool() -> None:
    """关闭PostgreSQL连接池"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PG连接池已关闭")
