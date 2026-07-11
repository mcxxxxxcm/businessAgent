"""PostgreSQL AsyncPostgresStore工厂 - 长期记忆(跨线程)

启用内置TTL机制:
- AsyncPostgresStore 表已有 expires_at 列和 idx_store_expires_at 索引
- 内置 start_ttl_sweeper() / sweep_ttl() 自动清理过期数据
- 只需传入 TTLConfig 即可激活
"""

import logging

from langgraph.store.postgres.aio import AsyncPostgresStore

from app.core.config import settings
from app.core._async_utils import async_init_singleton

logger = logging.getLogger(__name__)

_store: AsyncPostgresStore | None = None


async def _create_store() -> AsyncPostgresStore:
    """创建并初始化AsyncPostgresStore实例(含TTL配置)"""
    from langgraph.store.base import TTLConfig
    from app.core.postgres import get_pg_pool

    pool = await get_pg_pool()

    # 配置 TTL: 活跃用户自动续期，超期数据自动清理
    ttl_config = TTLConfig(
        default_ttl=settings.STORE_DEFAULT_TTL_MINUTES,
        refresh_on_read=True,
        sweep_interval_minutes=settings.STORE_SWEEP_INTERVAL_MINUTES,
    )

    store = AsyncPostgresStore(pool, ttl=ttl_config)
    await store.setup()

    # 启动 TTL 自动清理后台任务
    await store.start_ttl_sweeper()

    logger.info(
        "Store已初始化, TTL=%d分钟, 扫描间隔=%d分钟",
        settings.STORE_DEFAULT_TTL_MINUTES,
        settings.STORE_SWEEP_INTERVAL_MINUTES,
    )
    return store


async def get_store() -> AsyncPostgresStore:
    """获取AsyncPostgresStore实例(单例，竞态安全)"""
    return await async_init_singleton(globals(), "_store", _create_store)


async def close_store() -> None:
    """关闭Store并停止TTL清理"""
    global _store
    if _store is not None:
        try:
            await _store.stop_ttl_sweeper(timeout=5)
        except Exception:
            pass
        _store = None
        logger.info("Store已关闭")
