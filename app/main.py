"""FastAPI应用入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.exceptions import AgentError
from app.api.health import router as health_router
from app.api.chat import router as chat_router
from app.api.sessions import router as sessions_router
from app.api.middleware import RateLimitMiddleware, setup_cors

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    setup_logging(debug=settings.DEBUG)
    logger.info("启动 %s v%s", settings.APP_NAME, settings.APP_VERSION)

    # 启动时初始化连接
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
