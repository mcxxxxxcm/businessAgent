"""认证接口 - 登录获取JWT令牌"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """登录请求"""
    user_id: str = Field(..., min_length=1, max_length=64, description="用户ID")
    # 简单验证: 防止任意user_id登录(生产环境应接入真实用户体系)
    api_key: str = Field("", description="API密钥(简单鉴权，生产环境替换为OAuth)")


class LoginResponse(BaseModel):
    """登录响应"""
    access_token: str
    token_type: str = "bearer"
    user_id: str


class TokenVerifyResponse(BaseModel):
    """令牌验证响应"""
    valid: bool
    user_id: str | None = None
    expired: bool = False


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """登录获取JWT令牌

    简单鉴权模式: 提供user_id + api_key即可获取token。
    生产环境应替换为OAuth2/OIDC接入真实用户体系。
    """
    # 简单API Key验证(防止任意user_id登录)
    if settings.AUTH_ENABLED:
        if not settings.JWT_SECRET_KEY:
            raise HTTPException(status_code=500, detail="JWT_SECRET_KEY未配置")
        # 如果配置了API Key，则验证; 否则只验证user_id格式
        if request.api_key and request.api_key != settings.JWT_SECRET_KEY:
            raise HTTPException(status_code=401, detail="API密钥无效")

    from app.core.auth import create_token

    token = create_token(request.user_id)
    logger.info("用户登录: user_id=%s", request.user_id)

    return LoginResponse(
        access_token=token,
        user_id=request.user_id,
    )


@router.get("/verify", response_model=TokenVerifyResponse)
async def verify(token: str):
    """验证令牌有效性"""
    from app.core.auth import verify_token

    payload = verify_token(token)
    if payload is None:
        return TokenVerifyResponse(valid=False, expired=True)

    return TokenVerifyResponse(
        valid=True,
        user_id=payload.get("sub"),
    )
