"""中间件 - 限流、CORS等"""

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于Redis的IP级限流中间件"""

    async def dispatch(self, request: Request, call_next):
        # 仅对聊天接口限流
        if request.url.path.startswith("/api/v1/chat"):
            try:
                from app.core.redis import get_redis

                r = await get_redis()
                client_ip = request.client.host if request.client else "unknown"
                key = f"ratelimit:{client_ip}"
                current = await r.incr(key)
                if current == 1:
                    await r.expire(key, 60)
                if current > settings.RATE_LIMIT_PER_MINUTE:
                    raise HTTPException(
                        status_code=429,
                        detail="请求过于频繁，请稍后再试",
                    )
            except HTTPException:
                raise
            except Exception:
                # Redis不可用时跳过限流，不阻塞正常请求
                pass

        response = await call_next(request)
        return response


def setup_cors(app) -> None:
    """配置CORS"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境应限制域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
