"""FastAPI应用入口"""

import sys
import asyncio
import logging
from contextlib import asynccontextmanager

# Windows兼容: psycopg异步模式需要SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.exceptions import AgentError
from app.api.health import router as health_router
from app.api.chat import router as chat_router
from app.api.sessions import router as sessions_router
from app.api.outbound import router as outbound_router
from app.api.middleware import RateLimitMiddleware, setup_cors

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    setup_logging(debug=settings.DEBUG)
    logger.info("启动 %s v%s", settings.APP_NAME, settings.APP_VERSION)

    # 启动时预初始化连接池和Graph，避免首次请求超时
    try:
        from app.core.postgres import get_pg_pool
        from app.core.redis import get_redis

        # 先初始化PG连接池
        await get_pg_pool()
        logger.info("PG连接池初始化完成")

        # 初始化Redis
        await get_redis()
        logger.info("Redis初始化完成")

        # 预编译Graph(包含checkpointer和store的setup)
        from app.api.deps import get_graph
        await get_graph()
        logger.info("Agent Graph初始化完成")
    except Exception as e:
        logger.warning("预初始化失败(将在首次请求时重试): %s", e)

    yield

    # 关闭时清理资源
    logger.info("关闭连接池...")
    from app.core.redis import close_redis
    from app.core.postgres import close_pg_pool

    await close_redis()
    await close_pg_pool()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# 中间件
setup_cors(app)
app.add_middleware(RateLimitMiddleware)

# 路由
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(outbound_router)

# 静态文件
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    """前端首页"""
    return FileResponse(STATIC_DIR / "index.html")


# 全局异常处理
@app.exception_handler(AgentError)
async def agent_error_handler(request, exc: AgentError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=400,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": str(exc)}},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request, exc: Exception):
    from fastapi.responses import JSONResponse

    logger.exception("未处理异常")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误"}},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
