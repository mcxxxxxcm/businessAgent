"""数据库初始化脚本 - 创建必要的表和扩展"""

import asyncio
import sys
import os

# Windows兼容: psycopg异步模式需要SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings


async def init_database():
    """初始化PostgreSQL数据库

    1. 创建pgvector扩展(可选 - 需要PostgreSQL安装pgvector)
    2. LangGraph Checkpointer和Store的setup()会自动创建表
    """
    from psycopg_pool import AsyncConnectionPool
    from psycopg.rows import dict_row

    print(f"连接数据库: {settings.DATABASE_URL}")

    pool = AsyncConnectionPool(
        conninfo=settings.DATABASE_URL,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
        },
    )
    await pool.open()

    try:
        # 尝试创建pgvector扩展(可选)
        try:
            async with pool.connection() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                print("✓ pgvector扩展已创建")
        except Exception as e:
            print(f"⚠ pgvector扩展跳过(不影响核心功能): {e}")
            print("  提示: RAG向量检索功能暂不可用，其他功能正常")

        # 初始化LangGraph Checkpointer(对话持久化)
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        print("✓ LangGraph Checkpointer表已创建")

        # 初始化LangGraph Store(长期记忆)
        from langgraph.store.postgres.aio import AsyncPostgresStore
        store = AsyncPostgresStore(pool)
        await store.setup()
        print("✓ LangGraph Store表已创建")

        print("\n✅ 数据库初始化完成！")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(init_database())
