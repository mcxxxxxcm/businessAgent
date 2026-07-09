"""外呼管理器 - 通过FreeSWITCH主动拨打电话

流程:
1. Agent决定需要给用户打电话(或客服点击拨号)
2. 调用OutboundManager.place_call()
3. 通过ESL向FreeSWITCH发送originate命令
4. FreeSWITCH拨打用户电话
5. 用户接听后，桥接到语音网关(WebSocket)
6. 语音网关启动CallSession，AI开始对话

ESL (Event Socket Library):
- FreeSWITCH的事件套接字协议
- 通过TCP连接控制FreeSWITCH
- 可以发起呼叫、挂断、播放音频等
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class CallStatus(str, Enum):
    """外呼状态"""
    QUEUED = "queued"          # 排队中
    DIALING = "dialing"        # 拨号中
    RINGING = "ringing"        # 振铃中
    ANSWERED = "answered"      # 已接听
    FAILED = "failed"          # 呼叫失败
    BUSY = "busy"             # 用户忙线
    NO_ANSWER = "no_answer"   # 无人接听
    HANGUP = "hangup"         # 已挂断


@dataclass
class OutboundCall:
    """外呼记录"""
    call_id: str
    phone_number: str
    status: CallStatus = CallStatus.QUEUED
    gateway_session_id: Optional[str] = None  # 语音网关会话ID
    agent_session_id: Optional[str] = None    # Agent会话ID
    user_id: Optional[str] = None
    created_at: float = 0
    answered_at: float = 0
    ended_at: float = 0
    failure_reason: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class OutboundManager:
    """外呼管理器

    通过FreeSWITCH ESL协议发起外呼。
    支持批量外呼、排队、状态追踪。
    """

    def __init__(
        self,
        freeswitch_host: str = "127.0.0.1",
        freeswitch_esl_port: int = 8021,
        freeswitch_esl_password: str = "ClueCon",
        gateway_ws_url: str = "ws://127.0.0.1:8765",
        default_caller_id: str = "4001234567",  # 主叫号码(显示给用户)
    ):
        self.fs_host = freeswitch_host
        self.fs_port = freeswitch_esl_port
        self.fs_password = freeswitch_esl_password
        self.gateway_ws_url = gateway_ws_url
        self.default_caller_id = default_caller_id

        # 活跃外呼记录
        self._active_calls: dict[str, OutboundCall] = {}

        # ESL连接
        self._esl_reader: Optional[asyncio.StreamReader] = None
        self._esl_writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

    async def connect_esl(self) -> None:
        """连接FreeSWITCH ESL"""
        try:
            self._esl_reader, self._esl_writer = await asyncio.open_connection(
                self.fs_host, self.fs_port
            )

            # 读取欢迎消息
            welcome = await self._esl_reader.read(1024)
            logger.debug("ESL欢迎: %s", welcome.decode().strip())

            # 认证
            await self._send_esl_command(f"auth {self.fs_password}")
            response = await self._read_esl_response()
            if "+OK" not in response:
                raise ConnectionError(f"ESL认证失败: {response}")

            # 订阅事件
            await self._send_esl_command("event plain CHANNEL_CREATE CHANNEL_ANSWER CHANNEL_HANGUP ALL")
            await self._read_esl_response()

            self._connected = True
            logger.info("FreeSWITCH ESL连接成功: %s:%d", self.fs_host, self.fs_port)

        except Exception as e:
            logger.error("ESL连接失败: %s", e)
            self._connected = False
            raise

    async def disconnect_esl(self) -> None:
        """断开ESL连接"""
        if self._esl_writer:
            self._esl_writer.close()
            self._connected = False
            logger.info("ESL连接已断开")

    async def place_call(
        self,
        phone_number: str,
        user_id: Optional[str] = None,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> OutboundCall:
        """发起外呼

        Args:
            phone_number: 被叫号码
            user_id: 用户ID(用于关联Agent会话)
            caller_id: 主叫号码(显示给用户)
            metadata: 附加信息

        Returns:
            OutboundCall: 外呼记录
        """
        import time

        call_id = str(uuid.uuid4())[:12]
        call = OutboundCall(
            call_id=call_id,
            phone_number=phone_number,
            user_id=user_id or f"phone_{call_id[:8]}",
            created_at=time.time(),
            metadata=metadata or {},
        )
        self._active_calls[call_id] = call

        if not self._connected:
            # ESL未连接，使用模拟模式
            logger.warning("ESL未连接，使用模拟外呼模式")
            return await self._simulate_call(call)

        try:
            # 通过ESL发起originate命令
            caller = caller_id or self.default_caller_id

            # originate命令: 拨打SIP终端，接通后桥接到语音网关
            # 使用audio_stream将音频流导向WebSocket
            originate_cmd = (
                f"originate "
                f"{{origination_caller_id_number={caller},"
                f"execute_on_answer='audio_stream {self.gateway_ws_url}'}} "
                f"sofia/gateway/sip_provider/{phone_number} "
                f"&park()"
            )

            await self._send_esl_command(f"api {originate_cmd}")
            call.status = CallStatus.DIALING

            logger.info("外呼发起: call_id=%s, phone=%s", call_id, phone_number)

            # 启动后台任务监听ESL事件
            asyncio.create_task(self._monitor_call(call))

        except Exception as e:
            call.status = CallStatus.FAILED
            call.failure_reason = str(e)
            logger.error("外呼失败: call_id=%s, error=%s", call_id, e)

        return call

    async def place_call_simple(
        self,
        phone_number: str,
        user_id: Optional[str] = None,
        welcome_text: Optional[str] = None,
    ) -> OutboundCall:
        """简化版外呼 - 不依赖FreeSWITCH，直接通过语音网关WebSocket模拟

        适用场景: 开发测试、网页端拨号

        这个方法不拨打电话，而是创建一个"虚拟外呼"会话，
        前端可以通过WebSocket连接到语音网关进行对话。

        Args:
            phone_number: 被叫号码(仅用于记录)
            user_id: 用户ID
            welcome_text: 自定义欢迎语

        Returns:
            OutboundCall: 外呼记录(包含gateway_session_id用于连接)
        """
        import time

        call_id = str(uuid.uuid4())[:12]
        call = OutboundCall(
            call_id=call_id,
            phone_number=phone_number,
            user_id=user_id or f"phone_{call_id[:8]}",
            created_at=time.time(),
            gateway_session_id=call_id,  # 用call_id作为WebSocket会话标识
        )
        self._active_calls[call_id] = call
        call.status = CallStatus.QUEUED

        logger.info("虚拟外呼创建: call_id=%s, phone=%s", call_id, phone_number)
        return call

    async def hangup(self, call_id: str) -> None:
        """挂断外呼"""
        call = self._active_calls.get(call_id)
        if not call:
            return

        if self._connected and call.status in (CallStatus.DIALING, CallStatus.RINGING, CallStatus.ANSWERED):
            # 通过ESL挂断
            try:
                await self._send_esl_command(f"api uuid_kill {call_id}")
            except Exception as e:
                logger.warning("ESL挂断失败: %s", e)

        call.status = CallStatus.HANGUP
        import time
        call.ended_at = time.time()

    async def _simulate_call(self, call: OutboundCall) -> OutboundCall:
        """模拟外呼(ESL未连接时的降级方案)"""
        import time

        logger.info("模拟外呼: call_id=%s, phone=%s (3秒后自动接听)", call.call_id, call.phone_number)

        call.status = CallStatus.DIALING
        await asyncio.sleep(1)
        call.status = CallStatus.RINGING
        await asyncio.sleep(2)
        call.status = CallStatus.ANSWERED
        call.answered_at = time.time()

        logger.info("模拟外呼已接听: call_id=%s", call.call_id)
        return call

    async def _monitor_call(self, call: OutboundCall) -> None:
        """监听外呼ESL事件"""
        import time

        try:
            while call.status in (CallStatus.DIALING, CallStatus.RINGING, CallStatus.ANSWERED):
                response = await asyncio.wait_for(self._read_esl_response(), timeout=60)

                if "CHANNEL_ANSWER" in response:
                    call.status = CallStatus.ANSWERED
                    call.answered_at = time.time()
                    logger.info("外呼接听: call_id=%s", call.call_id)

                elif "CHANNEL_HANGUP" in response:
                    call.status = CallStatus.HANGUP
                    call.ended_at = time.time()
                    logger.info("外呼挂断: call_id=%s", call.call_id)
                    break

        except asyncio.TimeoutError:
            call.status = CallStatus.NO_ANSWER
            call.ended_at = time.time()
            logger.warning("外呼超时: call_id=%s", call.call_id)
        except Exception as e:
            logger.error("监听外呼异常: %s", e)

    async def _send_esl_command(self, command: str) -> None:
        """发送ESL命令"""
        if not self._esl_writer:
            raise ConnectionError("ESL未连接")

        self._esl_writer.write(f"{command}\n\n".encode())
        await self._esl_writer.drain()

    async def _read_esl_response(self) -> str:
        """读取ESL响应"""
        if not self._esl_reader:
            raise ConnectionError("ESL未连接")

        data = await self._esl_reader.read(4096)
        return data.decode()

    def get_call_status(self, call_id: str) -> Optional[OutboundCall]:
        """查询外呼状态"""
        return self._active_calls.get(call_id)

    def get_active_calls(self) -> list[OutboundCall]:
        """获取所有活跃外呼"""
        return [
            call for call in self._active_calls.values()
            if call.status in (CallStatus.DIALING, CallStatus.RINGING, CallStatus.ANSWERED)
        ]
