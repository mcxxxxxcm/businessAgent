"""数据库初始化脚本 - 创建必要的表和扩展"""

import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings


async def init_database():
    """初始化PostgreSQL数据库

    1. 创建pgvector扩展
    2. LangGraph Checkpointer和Store的setup()会在首次运行时自动创建表
    """
    from psycopg_pool import AsyncConnectionPool
    from psycopg.rows import dict_row

    print(f"连接数据库: {settings.DATABASE_URL}")

    pool = AsyncConnectionPool(
        conninfo=settings.DATABASE_URL,
        min_size=1,
        max_size=2,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
        },
    )
    await pool.open()

    try:
        async with pool.connection() as conn:
            # 创建pgvector扩展
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            print("✓ pgvector扩展已创建")

            # 验证扩展
            result = await conn.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            row = await result.fetchone()
            if row:
                print("✓ pgvector扩展验证成功")
            else:
                print("✗ pgvector扩展创建失败")

        # 初始化LangGraph Checkpointer
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        print("✓ LangGraph Checkpointer表已创建")

        # 初始化LangGraph Store
        from langgraph.store.postgres.aio import AsyncPostgresStore
        store = AsyncPostgresStore(pool)
        await store.setup()
        print("✓ LangGraph Store表已创建")

        print("\n数据库初始化完成！")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(init_database())
