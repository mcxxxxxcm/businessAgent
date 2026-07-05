"""Redis热缓存层 - 加速重复查询"""

import json
import logging

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


class SessionCache:
    """会话热缓存

    缓存策略:
    - 会话上下文: TTL 30分钟
    - 工具结果: TTL 5分钟
    """

    SESSION_TTL = 1800  # 30分钟
    TOOL_RESULT_TTL = 300  # 5分钟

    @staticmethod
    async def get_session_context(session_id: str) -> dict | None:
        """获取会话上下文缓存"""
        try:
            r = await get_redis()
            key = f"session:ctx:{session_id}"
            data = await r.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning("Redis读取会话缓存失败: %s", e)
        return None

    @staticmethod
    async def set_session_context(session_id: str, context: dict) -> None:
        """设置会话上下文缓存"""
        try:
            r = await get_redis()
            key = f"session:ctx:{session_id}"
            await r.setex(key, SessionCache.SESSION_TTL, json.dumps(context, ensure_ascii=False))
        except Exception as e:
            logger.warning("Redis写入会话缓存失败: %s", e)

    @staticmethod
    async def delete_session_context(session_id: str) -> None:
        """删除会话上下文缓存"""
        try:
            r = await get_redis()
            key = f"session:ctx:{session_id}"
            await r.delete(key)
        except Exception as e:
            logger.warning("Redis删除会话缓存失败: %s", e)

    @staticmethod
    async def get_tool_result(cache_key: str) -> dict | None:
        """获取工具结果缓存"""
        try:
            r = await get_redis()
            key = f"tool:cache:{cache_key}"
            data = await r.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning("Redis读取工具缓存失败: %s", e)
        return None

    @staticmethod
    async def set_tool_result(cache_key: str, result: dict) -> None:
        """设置工具结果缓存"""
        try:
            r = await get_redis()
            key = f"tool:cache:{cache_key}"
            await r.setex(
                key,
                SessionCache.TOOL_RESULT_TTL,
                json.dumps(result, ensure_ascii=False),
            )
        except Exception as e:
            logger.warning("Redis写入工具缓存失败: %s", e)

    @staticmethod
    async def set_user_online(user_id: str, session_id: str) -> None:
        """标记用户在线"""
        try:
            r = await get_redis()
            key = f"user:online:{user_id}"
            await r.setex(key, 300, session_id)  # 5分钟心跳
        except Exception:
            pass

    @staticmethod
    async def is_user_online(user_id: str) -> bool:
        """检查用户是否在线"""
        try:
            r = await get_redis()
            key = f"user:online:{user_id}"
            return await r.exists(key) > 0
        except Exception:
            return False
