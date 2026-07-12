"""健康检查接口 - 增强版

提供应用整体健康状况和各组件详细状态:
- Redis: 连接 + ping
- PostgreSQL: 连接池 + 简单查询
- 返回 degraded 状态时运维可感知
"""

import time

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """健康检查 - 检测Redis和PostgreSQL连接状态

    状态说明:
    - healthy: 所有组件正常
    - degraded: 部分组件不可用(如Redis宕机)，系统仍可运行但功能受限
    - unhealthy: 核心组件不可用(如PG宕机)
    """
    redis_status = "unavailable"
    redis_latency_ms = None
    postgres_status = "unavailable"
    postgres_latency_ms = None

    # 检查Redis
    try:
        from app.core.redis import get_redis

        r = await get_redis()
        t0 = time.monotonic()
        await r.ping()
        redis_latency_ms = int((time.monotonic() - t0) * 1000)
        redis_status = "ok"
    except Exception:
        pass

    # 检查PostgreSQL
    try:
        from app.core.postgres import get_pg_pool

        pool = await get_pg_pool()
        t0 = time.monotonic()
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
        postgres_latency_ms = int((time.monotonic() - t0) * 1000)
        postgres_status = "ok"
    except Exception:
        pass

    # 判断整体状态
    if postgres_status != "ok":
        # PG不可用 = 核心组件故障
        overall = "unhealthy"
    elif redis_status != "ok":
        # Redis不可用 = 降级运行(无限流/无缓存)
        overall = "degraded"
    else:
        overall = "healthy"

    result = {
        "status": overall,
        "version": settings.APP_VERSION,
        "redis": {
            "status": redis_status,
            "latency_ms": redis_latency_ms,
        },
        "postgres": {
            "status": postgres_status,
            "latency_ms": postgres_latency_ms,
        },
    }

    # unhealthy时返回503，方便负载均衡器摘除
    if overall == "unhealthy":
        from fastapi.responses import JSONResponse
        return JSONResponse(content=result, status_code=503)

    return result
