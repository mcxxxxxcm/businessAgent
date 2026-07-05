"""PostgreSQL连接池管理"""

from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from app.core.config import settings

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
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "prepare_threshold": 0,
            },
        )
        await _pool.open()
    return _pool


async def close_pg_pool() -> None:
    """关闭PostgreSQL连接池"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
