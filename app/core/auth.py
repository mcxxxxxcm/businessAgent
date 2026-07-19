"""JWT认证模块

提供:
- create_token(): 创建JWT令牌
- verify_token(): 验证JWT令牌
- get_current_user(): FastAPI依赖，从请求中提取当前用户
- mask_pii(): PII脱敏工具函数
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

logger = logging.getLogger(__name__)

_security = HTTPBearer(auto_error=False)


def create_token(user_id: str) -> str:
    """创建JWT令牌"""
    import jwt

    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """验证JWT令牌，返回payload或None"""
    import jwt

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT令牌已过期")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("JWT令牌无效: %s", e)
        return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> str:
    """FastAPI依赖: 从请求中提取当前认证用户ID

    - AUTH_ENABLED=False时跳过认证(开发模式)
    - 无token时返回匿名用户
    - token无效时返回401
    """
    # 认证关闭时，从请求体或query中取user_id(兼容开发模式)
    if not settings.AUTH_ENABLED:
        return getattr(request.state, "user_id", None) or "anonymous"

    # 无token
    if credentials is None:
        # 健康检查和静态文件不需要认证
        if request.url.path in ("/health", "/", "/api/v1/auth/login"):
            return "anonymous"
        raise HTTPException(status_code=401, detail="未提供认证令牌")

    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="认证令牌无效或已过期")

    user_id = payload.get("sub", "anonymous")
    # 存入request.state供后续使用
    request.state.user_id = user_id
    return user_id


# === PII脱敏 ===

def mask_phone(phone: str) -> str:
    """手机号脱敏: 13812345678 → 138****5678"""
    if len(phone) == 11 and phone.isdigit():
        return f"{phone[:3]}****{phone[7:]}"
    return phone


def mask_pii(text: str) -> str:
    """通用PII脱敏: 自动识别文本中的手机号、身份证号、银行卡号并脱敏"""
    # 手机号: 1开头11位数字
    text = re.sub(r'(1[3-9]\d)\d{4}(\d{4})', r'\1****\2', text)
    # 身份证号: 18位，末位可能是X
    text = re.sub(r'(\d{4})\d{10}(\d{3}[\dXx])', r'\1**********\2', text)
    # 银行卡号: 16-19位数字(保留前4后4)
    text = re.sub(r'(\d{4})\d{8,11}(\d{4})', r'\1********\2', text)
    return text
