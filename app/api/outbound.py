"""外呼API接口 - 发起电话外呼"""

import json
import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calls", tags=["outbound"])


class PlaceCallRequest(BaseModel):
    """外呼请求"""
    phone_number: str = Field(..., description="被叫号码", pattern=r"^1[3-9]\d{9}$")
    user_id: Optional[str] = Field(None, description="用户ID(关联Agent会话)")
    caller_id: Optional[str] = Field(None, description="主叫号码(显示给用户)")
    welcome_text: Optional[str] = Field(None, description="自定义欢迎语")
    metadata: Optional[dict] = Field(None, description="附加信息")


class CallStatusResponse(BaseModel):
    """外呼状态响应"""
    call_id: str
    phone_number: str
    status: str
    user_id: Optional[str] = None
    failure_reason: Optional[str] = None
    gateway_session_id: Optional[str] = None


@router.post("/place", response_model=CallStatusResponse)
async def place_call(request: PlaceCallRequest):
    """发起外呼

    通过FreeSWITCH拨打用户电话，接通后AI自动开始对话。
    如果FreeSWITCH未连接，返回虚拟会话ID(开发测试用)。
    """
    from app.voice.outbound import OutboundManager, CallStatus
    from app.api.deps import get_outbound_manager

    manager = await get_outbound_manager()

    call = await manager.place_call(
        phone_number=request.phone_number,
        user_id=request.user_id,
        caller_id=request.caller_id,
        metadata=request.metadata,
    )

    return CallStatusResponse(
        call_id=call.call_id,
        phone_number=call.phone_number,
        status=call.status.value,
        user_id=call.user_id,
        failure_reason=call.failure_reason,
        gateway_session_id=call.gateway_session_id,
    )


@router.get("/status/{call_id}", response_model=CallStatusResponse)
async def get_call_status(call_id: str):
    """查询外呼状态"""
    from app.api.deps import get_outbound_manager

    manager = await get_outbound_manager()
    call = manager.get_call_status(call_id)

    if not call:
        return CallStatusResponse(
            call_id=call_id,
            phone_number="",
            status="not_found",
        )

    return CallStatusResponse(
        call_id=call.call_id,
        phone_number=call.phone_number,
        status=call.status.value,
        user_id=call.user_id,
        failure_reason=call.failure_reason,
        gateway_session_id=call.gateway_session_id,
    )


@router.post("/hangup/{call_id}")
async def hangup_call(call_id: str):
    """挂断外呼"""
    from app.api.deps import get_outbound_manager

    manager = await get_outbound_manager()
    await manager.hangup(call_id)

    return {"status": "ok", "call_id": call_id}


@router.get("/active")
async def list_active_calls():
    """列出所有活跃外呼"""
    from app.api.deps import get_outbound_manager

    manager = await get_outbound_manager()
    calls = manager.get_active_calls()

    return {
        "count": len(calls),
        "calls": [
            {
                "call_id": c.call_id,
                "phone_number": c.phone_number,
                "status": c.status.value,
                "user_id": c.user_id,
            }
            for c in calls
        ],
    }
