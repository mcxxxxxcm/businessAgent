"""Agent客户端 - 调用现有文字客服Agent获取回复

Voice Gateway不重新实现Agent逻辑，而是直接调用
现有的 /api/v1/chat/stream 接口，复用所有Agent能力。
"""

import json
import logging
from typing import AsyncIterator, Optional

import aiohttp

logger = logging.getLogger(__name__)


class AgentClient:
    """Agent API客户端

    通过HTTP调用现有Agent服务，支持:
    1. SSE流式调用 - 逐token获取回复(低延迟)
    2. 同步调用 - 一次性获取完整回复
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取HTTP会话(懒初始化)"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def chat_stream(
        self,
        message: str,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """流式调用Agent (SSE)

        逐token获取回复文本，适用于语音场景:
        - 收到第一个token即可开始TTS合成
        - 降低端到端延迟

        Args:
            message: 用户消息文本
            user_id: 用户ID
            session_id: 会话ID (None则新建)

        Yields:
            回复文本片段(token by token)
        """
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/chat/stream"

        payload = {
            "message": message,
            "user_id": user_id,
            "session_id": session_id or "",
        }

        try:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error("Agent API调用失败: status=%d, body=%s", resp.status, error_text[:200])
                    yield "抱歉，系统暂时无法处理您的请求。"
                    return

                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue

                    # 解析SSE格式: "data: {...}"
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            event_type = data.get("type", "")

                            if event_type == "token":
                                content = data.get("content", "")
                                if content:
                                    yield content
                            elif event_type == "session":
                                # 会话信息，忽略
                                pass
                            elif event_type == "done":
                                break
                        except json.JSONDecodeError:
                            continue

        except aiohttp.ClientError as e:
            logger.error("Agent API连接失败: %s", e)
            yield "抱歉，网络连接异常，请稍后再试。"
        except Exception as e:
            logger.error("Agent调用异常: %s", e)
            yield "抱歉，系统出现异常，请稍后再试。"

    async def chat_sync(
        self,
        message: str,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> tuple[str, str]:
        """同步调用Agent (获取完整回复)

        Returns:
            (reply_text, session_id)
        """
        full_text = ""
        actual_session_id = session_id or ""

        async for token in self.chat_stream(message, user_id, session_id):
            full_text += token

        return full_text, actual_session_id

    async def close(self):
        """关闭HTTP会话"""
        if self._session and not self._session.closed:
            await self._session.close()
