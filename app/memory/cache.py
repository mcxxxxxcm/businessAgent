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

    # === 反馈相关 ===

    FEEDBACK_TTL = 604800  # 7天

    @staticmethod
    async def store_feedback(session_id: str, message_id: str, feedback: dict) -> None:
        """存储用户反馈"""
        try:
            r = await get_redis()
            key = f"feedback:{session_id}:{message_id}"
            await r.setex(key, SessionCache.FEEDBACK_TTL, json.dumps(feedback, ensure_ascii=False))
        except Exception as e:
            logger.warning("Redis存储反馈失败: %s", e)

    @staticmethod
    async def get_session_negative_count(session_id: str) -> int:
        """获取session内negative反馈次数"""
        try:
            r = await get_redis()
            key = f"feedback:negative:{session_id}"
            count = await r.get(key)
            return int(count) if count else 0
        except Exception:
            return 0

    @staticmethod
    async def increment_negative_count(session_id: str) -> int:
        """递增session的negative反馈计数，返回当前值"""
        try:
            r = await get_redis()
            key = f"feedback:negative:{session_id}"
            count = await r.incr(key)
            await r.expire(key, SessionCache.FEEDBACK_TTL)
            return count
        except Exception:
            return 0

    # === 降级链统计 ===

    @staticmethod
    async def incr_degradation_stat(schema_name: str, layer: int, result: str) -> None:
        """记录降级链每层结果

        Args:
            schema_name: Pydantic模型名(如IntentClassification)
            layer: 层号(1-4)
            result: "ok" 或 "fail"
        """
        try:
            r = await get_redis()
            key = f"stats:degradation:{schema_name}:layer{layer}:{result}"
            await r.incr(key)
        except Exception:
            pass  # 统计失败不阻塞主流程

    @staticmethod
    async def get_degradation_stats() -> dict:
        """获取降级链统计(聚合所有schema和layer的命中率)"""
        try:
            r = await get_redis()
            keys = []
            async for key in r.scan_iter(match="stats:degradation:*"):
                keys.append(key)
            if not keys:
                return {}

            values = await r.mget(keys)
            stats = {}
            for key, val in zip(keys, values):
                # key格式: stats:degradation:{schema}:layer{N}:{ok|fail}
                parts = key.decode() if isinstance(key, bytes) else key
                parts = parts.split(":")
                if len(parts) >= 5:
                    schema = parts[2]
                    layer_result = f"{parts[3]}:{parts[4]}"
                    if schema not in stats:
                        stats[schema] = {}
                    stats[schema][layer_result] = int(val) if val else 0
            return stats
        except Exception:
            return {}
