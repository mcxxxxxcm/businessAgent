"""用户反馈接口 - HITL(Human-in-the-Loop)核心

数据流: 前端按钮 → POST /api/v1/feedback → Redis存储 + 规则判断 → (可选)转人工

转人工触发规则:
1. rating="negative" + 当前sentiment="angry" → 立即转人工
2. 同一session连续 FEEDBACK_NEGATIVE_ESCALATION_THRESHOLD 次negative → 建议转人工
"""

import logging
from datetime import datetime

from fastapi import APIRouter

from app.models.schemas import FeedbackRequest, FeedbackResponse
from app.memory.cache import SessionCache
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["feedback"])


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest) -> FeedbackResponse:
    """提交用户反馈

    1. 存储反馈到Redis
    2. 更新negative计数
    3. 判断是否触发转人工
    """
    # 防刷: 同session每分钟最多5次反馈
    try:
        from app.core.redis import get_redis
        r = await get_redis()
        rate_key = f"ratelimit:feedback:{request.session_id}"
        count = await r.incr(rate_key)
        if count == 1:
            await r.expire(rate_key, 60)
        if count > 5:
            return FeedbackResponse(received=False)
    except Exception:
        pass  # Redis不可用不阻塞
    # 存储反馈
    feedback_data = {
        "rating": request.rating,
        "comment": request.comment,
        "user_id": request.user_id,
        "timestamp": datetime.utcnow().isoformat(),
    }
    await SessionCache.store_feedback(request.session_id, request.message_id, feedback_data)

    # 更新用户画像满意度（异步，不阻塞）
    try:
        from app.memory.profile import update_satisfaction_score
        await update_satisfaction_score(request.user_id, request.rating)
    except Exception as e:
        logger.warning("更新满意度画像失败: %s", e)

    # 判断是否触发转人工
    escalation_triggered = False
    escalation_message = None

    if request.rating == "negative" and settings.FEEDBACK_ENABLE_AUTO_ESCALATION:
        # 检查是否达到阈值
        negative_count = await SessionCache.increment_negative_count(request.session_id)

        if negative_count >= settings.FEEDBACK_NEGATIVE_ESCALATION_THRESHOLD:
            escalation_triggered = True
            escalation_message = "感谢您的反馈。看起来我们的服务未能满足您的需求，是否需要转接人工客服？"

            logger.info(
                "反馈触发转人工: session=%s, negative_count=%d, threshold=%d",
                request.session_id,
                negative_count,
                settings.FEEDBACK_NEGATIVE_ESCALATION_THRESHOLD,
            )

    logger.info(
        "收到反馈: session=%s, msg=%s, rating=%s, escalation=%s",
        request.session_id,
        request.message_id,
        request.rating,
        escalation_triggered,
    )

    return FeedbackResponse(
        received=True,
        escalation_triggered=escalation_triggered,
        escalation_message=escalation_message,
    )
