"""转人工队列服务 - 管理人工客服排队"""

import json
import logging

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


class EscalationQueue:
    """转人工排队管理

    使用Redis List实现FIFO队列，
    支持按优先级排序。
    """

    QUEUE_KEY = "escalation:queue"
    AGENT_KEY = "escalation:agents"  # 在线人工客服

    @staticmethod
    async def enqueue(session_id: str, user_id: str, priority: str = "normal", reason: str = "") -> dict:
        """加入转人工队列"""
        r = await get_redis()
        item = json.dumps(
            {
                "session_id": session_id,
                "user_id": user_id,
                "priority": priority,
                "reason": reason,
            },
            ensure_ascii=False,
        )

        # 高优先级插入队首
        if priority in ("high", "urgent"):
            await r.lpush(EscalationQueue.QUEUE_KEY, item)
        else:
            await r.rpush(EscalationQueue.QUEUE_KEY, item)

        # 获取队列位置
        position = await EscalationQueue.get_position(session_id)
        return {"position": position, "estimated_wait_minutes": position * 3}

    @staticmethod
    async def get_position(session_id: str) -> int:
        """获取在队列中的位置"""
        r = await get_redis()
        items = await r.lrange(EscalationQueue.QUEUE_KEY, 0, -1)
        for i, item in enumerate(items):
            data = json.loads(item)
            if data.get("session_id") == session_id:
                return i + 1
        return -1  # 不在队列中

    @staticmethod
    async def dequeue() -> dict | None:
        """取出队列头部(分配给人工客服)"""
        r = await get_redis()
        item = await r.lpop(EscalationQueue.QUEUE_KEY)
        if item:
            return json.loads(item)
        return None

    @staticmethod
    async def get_queue_length() -> int:
        """获取队列长度"""
        r = await get_redis()
        return await r.llen(EscalationQueue.QUEUE_KEY)
