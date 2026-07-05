"""PostgreSQL AsyncPostgresStore工厂 - 长期记忆(跨线程)"""

from langgraph.store.postgres.aio import AsyncPostgresStore

from app.core.config import settings

_store: AsyncPostgresStore | None = None


async def get_store() -> AsyncPostgresStore:
    """获取AsyncPostgresStore实例(单例)

    用于存储用户偏好、历史摘要等跨会话数据。
    命名空间示例: ("users", "{user_id}", "preferences")
    """
    global _store
    if _store is not None:
        return _store

    from app.core.postgres import get_pg_pool

    pool = await get_pg_pool()
    _store = AsyncPostgresStore(pool)
    await _store.setup()

    return _store
