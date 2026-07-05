"""健康检查接口"""

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """健康检查 - 检测Redis和PostgreSQL连接状态"""
    redis_status = "unavailable"
    postgres_status = "unavailable"

    # 检查Redis
    try:
        from app.core.redis import get_redis

        r = await get_redis()
        await r.ping()
        redis_status = "ok"
    except Exception:
        pass

    # 检查PostgreSQL
    try:
        from app.core.postgres import get_pg_pool

        pool = await get_pg_pool()
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
        postgres_status = "ok"
    except Exception:
        pass

    overall = "healthy" if redis_status == "ok" and postgres_status == "ok" else "degraded"

    return {
        "status": overall,
        "redis": redis_status,
        "postgres": postgres_status,
        "version": settings.APP_VERSION,
    }
