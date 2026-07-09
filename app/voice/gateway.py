"""语音网关 - WebSocket服务器

接收来自FreeSWITCH或浏览器客户端的音频流，
通过VAD→STT→Agent→TTS管道处理后，返回合成音频。

支持两种接入方式:
1. FreeSWITCH (生产): 通过mod_audio_stream连接WebSocket
2. 浏览器 (开发/测试): 前端麦克风直接连接WebSocket

协议:
- 输入: PCM 16bit {sample_rate}Hz 单声道音频字节
- 输出: PCM 16bit 16kHz 单声道音频字节
- 控制消息: JSON格式文本帧
"""

import asyncio
import json
import logging
import uuid
from typing import Optional

import websockets
from websockets.asyncio.server import Server, ServerConnection

from app.voice.config import VoiceConfig
from app.voice.call_session import CallSession, CallState

logger = logging.getLogger(__name__)


class VoiceGateway:
    """语音网关WebSocket服务器

    每个WebSocket连接对应一个CallSession(一次通话)。
    """

    def __init__(self, config: Optional[VoiceConfig] = None):
        self.config = config or VoiceConfig()
        self._sessions: dict[str, CallSession] = {}
        self._server: Optional[Server] = None
        self._active_calls = 0

    async def start(self) -> None:
        """启动WebSocket服务器"""
        self._server = await websockets.serve(
            self._handle_connection,
            self.config.host,
            self.config.port,
            max_size=None,  # 不限制消息大小(音频流可能很大)
            ping_interval=20,
            ping_timeout=60,
        )
        logger.info(
            "语音网关启动: ws://%s:%d (最大并发=%d)",
            self.config.host,
            self.config.port,
            self.config.max_concurrent_calls,
        )

    async def stop(self) -> None:
        """停止服务器"""
        # 结束所有活跃通话
        for call_id, session in list(self._sessions.items()):
            await session.end("server_shutdown")

        if self._server:
            self._server.close()
            await self._server.wait_closed()

        logger.info("语音网关已停止")

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        """处理一个WebSocket连接(一次通话)"""
        call_id = str(uuid.uuid4())[:12]

        # 检查并发限制
        if self._active_calls >= self.config.max_concurrent_calls:
            logger.warning("并发通话数已达上限: %d", self.config.max_concurrent_calls)
            await websocket.close(1013, "Too many concurrent calls")
            return

        self._active_calls += 1

        # 音频输出队列(线程安全)
        audio_queue: asyncio.Queue[bytes] = asyncio.Queue()

        # 音频输出回调
        def audio_output_callback(audio_data: bytes) -> None:
            audio_queue.put_nowait(audio_data)

        # 事件回调
        def event_callback(event_type: str, data: dict) -> None:
            try:
                # 通过WebSocket发送事件通知(JSON文本帧)
                msg = json.dumps({"type": event_type, **data}, ensure_ascii=False)
                asyncio.create_task(self._safe_send(websocket, msg))
            except Exception as e:
                logger.warning("发送事件失败: %s", e)

        # 创建通话会话
        session = CallSession(
            call_id=call_id,
            config=self.config,
            audio_output_callback=audio_output_callback,
            event_callback=event_callback,
        )
        self._sessions[call_id] = session

        try:
            # 发送连接确认
            await websocket.send(json.dumps({
                "type": "connected",
                "call_id": call_id,
                "sample_rate": self.config.sample_rate,
            }))

            # 等待客户端发送开始信号
            start_msg = await asyncio.wait_for(websocket.recv(), timeout=30)
            start_data = json.loads(start_msg) if isinstance(start_msg, str) else {}
            caller_number = start_data.get("caller", "unknown")

            # 启动通话
            await session.start(caller_number)

            # 启动音频发送任务
            send_task = asyncio.create_task(self._send_audio_loop(websocket, audio_queue))

            # 接收音频循环
            async for message in websocket:
                if session.state == CallState.ENDED:
                    break

                if isinstance(message, bytes):
                    # 二进制帧 = 音频数据
                    await session.process_audio(message, self.config.sample_rate)
                elif isinstance(message, str):
                    # 文本帧 = 控制消息
                    try:
                        ctrl = json.loads(message)
                        await self._handle_control(session, ctrl)
                    except json.JSONDecodeError:
                        pass

        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket连接关闭: call_id=%s", call_id)
        except asyncio.TimeoutError:
            logger.warning("等待开始信号超时: call_id=%s", call_id)
        except Exception as e:
            logger.error("通话处理异常: call_id=%s, error=%s", call_id, e)
        finally:
            # 清理
            send_task.cancel()
            await session.end("websocket_closed")
            self._sessions.pop(call_id, None)
            self._active_calls -= 1

    async def _send_audio_loop(
        self,
        websocket: ServerConnection,
        audio_queue: asyncio.Queue[bytes],
    ) -> None:
        """持续从队列取音频并发送给客户端"""
        try:
            while True:
                audio_data = await audio_queue.get()
                try:
                    await websocket.send(audio_data)
                except websockets.exceptions.ConnectionClosed:
                    break
        except asyncio.CancelledError:
            pass

    async def _handle_control(self, session: CallSession, ctrl: dict) -> None:
        """处理控制消息"""
        msg_type = ctrl.get("type", "")

        if msg_type == "hangup":
            await session.end("user_hangup")
        elif msg_type == "dtmf":
            # DTMF按键
            digit = ctrl.get("digit", "")
            logger.info("DTMF: call_id=%s, digit=%s", session.call_id, digit)
        elif msg_type == "start":
            # 开始信号(已在外层处理)
            pass

    async def _safe_send(self, websocket: ServerConnection, message: str) -> None:
        """安全发送文本消息"""
        try:
            await websocket.send(message)
        except websockets.exceptions.ConnectionClosed:
            pass

    def get_stats(self) -> dict:
        """获取网关统计信息"""
        return {
            "active_calls": self._active_calls,
            "max_concurrent": self.config.max_concurrent_calls,
            "sessions": {
                call_id: {
                    "state": session.state.value,
                    "turn_count": session.turn_count,
                    "caller": session.caller_number,
                }
                for call_id, session in self._sessions.items()
            },
        }


async def main():
    """启动语音网关(独立进程)"""
    import sys

    # Windows事件循环策略
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = VoiceConfig()
    gateway = VoiceGateway(config)

    try:
        await gateway.start()
        logger.info("语音网关运行中，按Ctrl+C停止")
        # 永远等待
        await asyncio.Future()
    except KeyboardInterrupt:
        logger.info("收到停止信号")
    finally:
        await gateway.stop()


if __name__ == "__main__":
    asyncio.run(main())
