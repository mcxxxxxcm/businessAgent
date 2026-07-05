"""PostgreSQL AsyncPostgresSaver工厂 - 短期记忆(对话持久化)"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings

_checkpointer: AsyncPostgresSaver | None = None


async def get_checkpointer() -> AsyncPostgresSaver:
    """获取AsyncPostgresSaver实例(单例)

    使用AsyncConnectionPool管理数据库连接，
    避免长时间运行的工作流导致连接超时。
    首次调用时会执行setup()创建必要的数据库表。
    """
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    from app.core.postgres import get_pg_pool

    pool = await get_pg_pool()
    _checkpointer = AsyncPostgresSaver(pool)
    await _checkpointer.setup()

    return _checkpointer
