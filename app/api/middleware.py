"""中间件 - 限流、CORS等

安全措施:
1. 限流: Redis Lua脚本保证incr+expire原子性，避免key永不过期
2. 降级: Redis不可用时可配置策略(allow放行/deny拒绝)
3. CORS: 支持白名单配置，杜绝* + credentials的CSRF风险
"""

import logging

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)

# Redis Lua脚本: 原子化 incr + expire
# 解决 incr 和 expire 之间 crash 导致 key 永不过期的问题
_RATE_LIMIT_LUA = """
local current = redis.call('incr', KEYS[1])
if current == 1 then
    redis.call('expire', KEYS[1], ARGV[1])
end
return current
"""

# 限流降级计数器(进程内缓存，真实值在Redis)
_rate_limit_fallback_count = 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于Redis的IP级限流中间件

    安全措施:
    - 使用Lua脚本保证 incr+expire 原子性
    - Redis不可用时根据 RATE_LIMIT_REDIS_FALLBACK 策略处理
    - 降级事件记录 logger.error + 进程内计数器
    """

    async def dispatch(self, request: Request, call_next):
        # 仅对聊天接口限流
        if request.url.path.startswith("/api/v1/chat"):
            try:
                from app.core.redis import get_redis

                r = await get_redis()
                client_ip = request.client.host if request.client else "unknown"
                key = f"ratelimit:{client_ip}"

                # Lua脚本原子化执行 incr + expire
                current = await r.eval(
                    _RATE_LIMIT_LUA, 1, key, 60  # 1个key, 过期60秒
                )

                if current > settings.RATE_LIMIT_PER_MINUTE:
                    raise HTTPException(
                        status_code=429,
                        detail="请求过于频繁，请稍后再试",
                    )

            except HTTPException:
                raise
            except Exception as e:
                # Redis不可用 — 不再静默pass，同时持久化计数到Redis(尝试)
                global _rate_limit_fallback_count
                _rate_limit_fallback_count += 1
                # 尝试将计数持久化到Redis(重启不丢失)
                try:
                    from app.core.redis import get_redis
                    r = await get_redis()
                    total = await r.incr("stats:rate_limit:fallback_count")
                    _rate_limit_fallback_count = total  # 同步进程内缓存
                except Exception:
                    pass  # Redis也不可用，只能靠进程内计数
                logger.error(
                    "限流Redis不可用(累计%d次), 降级策略=%s, 错误: %s",
                    _rate_limit_fallback_count,
                    settings.RATE_LIMIT_REDIS_FALLBACK,
                    e,
                )

                if settings.RATE_LIMIT_REDIS_FALLBACK == "deny":
                    raise HTTPException(
                        status_code=503,
                        detail="服务暂时不可用，请稍后重试",
                    )
                # "allow" 策略: 放行但已有日志记录

        response = await call_next(request)
        return response


def setup_cors(app) -> None:
    """配置CORS — 安全优先

    规则:
    - 配置了 CORS_ALLOWED_ORIGINS → 使用白名单 + allow_credentials=True
    - 未配置(空) → allow_origins=["*"] + allow_credentials=False (防止CSRF)
    - DEBUG模式 → 同上，开发时用*但不带credentials
    """
    allowed_origins = settings.CORS_ALLOWED_ORIGINS
    if allowed_origins:
        # 生产模式: 白名单 + 允许credentials
        origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
        allow_credentials = True
    else:
        # 开发/默认模式: 允许所有来源，但不带credentials (防CSRF)
        origins = ["*"]
        allow_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.info(
        "CORS配置: origins=%s, credentials=%s",
        origins,
        allow_credentials,
    )
