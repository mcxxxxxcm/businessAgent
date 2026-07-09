"""通话会话 - 单次电话通话的完整生命周期管理

状态机:
  RINGING → CONNECTED → LISTENING → PROCESSING → SPEAKING → LISTENING → ... → ENDED

核心流程(全双工):
  1. 连接建立 → 播放欢迎语
  2. LISTENING: 接收音频 → VAD检测 → STT识别
  3. 用户说完 → PROCESSING: 调用Agent获取回复
  4. 回复到达 → SPEAKING: TTS合成 → 播放音频
  5. 播放中检测打断 → 回到LISTENING
  6. 超时/挂断 → ENDED
"""

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Callable, Optional

from app.voice.config import VoiceConfig
from app.voice.vad import BaseVAD, VoiceState, create_vad
from app.voice.stt import BaseSTT, create_stt
from app.voice.tts import BaseTTS, create_tts
from app.voice.agent_client import AgentClient
from app.voice.audio import resample, pcm_to_wav

logger = logging.getLogger(__name__)


class CallState(str, Enum):
    """通话状态"""
    RINGING = "ringing"        # 振铃中
    CONNECTED = "connected"    # 已接通
    LISTENING = "listening"    # 听用户说话
    PROCESSING = "processing"  # Agent处理中
    SPEAKING = "speaking"      # AI播放语音中
    ENDED = "ended"           # 通话结束


class CallSession:
    """单次通话会话

    管理一次电话通话的完整生命周期，
    包括音频处理管道(VAD→STT→Agent→TTS)和状态转换。
    """

    def __init__(
        self,
        call_id: str,
        config: VoiceConfig,
        audio_output_callback: Callable[[bytes], None],
        event_callback: Optional[Callable[[str, dict], None]] = None,
    ):
        """
        Args:
            call_id: 通话唯一ID
            config: 语音配置
            audio_output_callback: 音频输出回调(发送给FreeSWITCH/客户端)
            event_callback: 事件回调(通话状态变化等)
        """
        self.call_id = call_id
        self.config = config
        self._audio_output = audio_output_callback
        self._event_callback = event_callback

        # 通话状态
        self.state = CallState.RINGING
        self.started_at: float = 0
        self.ended_at: float = 0
        self.turn_count: int = 0
        self.session_id: Optional[str] = None  # Agent会话ID

        # 用户信息
        self.user_id = f"{config.agent_user_id_prefix}{call_id[:8]}"
        self.caller_number: Optional[str] = None

        # 音频处理组件
        self.vad: BaseVAD = create_vad(
            engine=config.vad_engine,
            sample_rate=config.sample_rate,
            frame_duration_ms=config.frame_duration_ms,
            silence_ms=config.vad_silence_ms,
            speech_ms=config.vad_speech_ms,
            threshold=config.vad_threshold,
        )
        self.stt: BaseSTT = create_stt(
            engine=config.stt_engine,
            model=config.funasr_model,
            model_revision=config.funasr_model_revision,
            sample_rate=config.sample_rate,
        )
        self.tts: BaseTTS = create_tts(
            engine=config.tts_engine,
            voice=config.edge_tts_voice,
            rate=config.edge_tts_rate,
            sample_rate=config.sample_rate,
            cosyvoice_model_dir=config.cosyvoice_model_dir,
        )
        self.agent: AgentClient = AgentClient(base_url=config.agent_api_url)

        # 音频缓冲(用于录音)
        self._user_audio_buffer = b""
        self._ai_audio_buffer = b""

        # 任务控制
        self._tts_task: Optional[asyncio.Task] = None
        self._agent_task: Optional[asyncio.Task] = None
        self._should_stop = False

        # STT音频缓冲(累积到一帧再送)
        self._stt_buffer = b""
        self._stt_frame_size = int(config.sample_rate * config.frame_duration_ms / 1000) * 2

    async def start(self, caller_number: Optional[str] = None) -> None:
        """开始通话"""
        self.state = CallState.CONNECTED
        self.started_at = time.time()
        self.caller_number = caller_number

        await self.stt.start_stream()

        self._emit_event("call_started", {
            "call_id": self.call_id,
            "caller": caller_number,
        })

        logger.info("通话开始: call_id=%s, caller=%s", self.call_id, caller_number)

        # 播放欢迎语
        await self._speak(config.welcome_text)
        self._transition(CallState.LISTENING)

    async def process_audio(self, audio_data: bytes, sample_rate: int = 16000) -> None:
        """处理从FreeSWITCH/客户端收到的音频数据

        这是音频输入的主入口，由Gateway调用。
        每次WebSocket收到音频chunk时调用一次。

        Args:
            audio_data: PCM 16bit 音频字节
            sample_rate: 音频采样率
        """
        if self._should_stop or self.state == CallState.ENDED:
            return

        # 重采样到目标采样率
        if sample_rate != self.config.sample_rate:
            audio_data = resample(audio_data, sample_rate, self.config.sample_rate)

        # 累积到录音缓冲
        if self.config.recording_enabled:
            self._user_audio_buffer += audio_data

        # 根据当前状态处理
        if self.state == CallState.LISTENING:
            await self._process_listening(audio_data)
        elif self.state == CallState.SPEAKING:
            await self._process_speaking(audio_data)

    async def end(self, reason: str = "normal") -> None:
        """结束通话"""
        if self.state == CallState.ENDED:
            return

        self._should_stop = True
        self.state = CallState.ENDED
        self.ended_at = time.time()

        # 停止TTS
        await self.tts.stop()

        # 取消进行中的任务
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()

        # 停止STT
        await self.stt.stop_stream()

        # 保存录音
        if self.config.recording_enabled:
            await self._save_recording()

        duration = self.ended_at - self.started_at if self.started_at else 0
        self._emit_event("call_ended", {
            "call_id": self.call_id,
            "reason": reason,
            "duration_sec": round(duration, 1),
            "turn_count": self.turn_count,
        })

        logger.info(
            "通话结束: call_id=%s, reason=%s, duration=%.1fs, turns=%d",
            self.call_id, reason, duration, self.turn_count,
        )

    # ===== 内部方法 =====

    async def _process_listening(self, audio_data: bytes) -> None:
        """LISTENING状态: 运行VAD+STT"""
        # 送入VAD检测
        vad_results = self.vad.process_audio(audio_data)

        # 累积音频到STT缓冲
        self._stt_buffer += audio_data

        for result in vad_results:
            if result.state == VoiceState.END_OF_SPEECH:
                # 用户说完了一句
                logger.debug("VAD: END_OF_SPEECH (prob=%.2f)", result.probability)
                await self._on_user_finished_speaking()
                return

            elif result.state == VoiceState.SPEECH:
                # 用户正在说话 → 送STT(流式)
                if len(self._stt_buffer) >= self._stt_frame_size:
                    chunk = self._stt_buffer[:self._stt_frame_size]
                    self._stt_buffer = self._stt_buffer[self._stt_frame_size:]
                    # 流式识别(中间结果，暂不使用)
                    _ = await self.stt.feed_audio(chunk)

    async def _process_speaking(self, audio_data: bytes) -> None:
        """SPEAKING状态: 检测打断"""
        # 运行VAD检测用户是否在说话
        vad_results = self.vad.process_audio(audio_data)

        for result in vad_results:
            if result.state == VoiceState.SPEECH:
                # 检测到用户在说话 → 打断AI
                logger.info("检测到打断: call_id=%s", self.call_id)
                await self._on_barge_in()
                return

    async def _on_user_finished_speaking(self) -> None:
        """用户说完了一句话"""
        # 把STT缓冲区剩余音频也送进去
        if self._stt_buffer:
            await self.stt.feed_audio(self._stt_buffer)
            self._stt_buffer = b""

        # 获取最终识别文本
        user_text = await self.stt.finalize()
        user_text = user_text.strip()

        # 重置VAD和STT，准备下一轮
        self.vad.reset()
        await self.stt.start_stream()

        if not user_text:
            # 空文本，继续听
            logger.debug("STT返回空文本，继续听")
            return

        self.turn_count += 1
        self._transition(CallState.PROCESSING)
        self._emit_event("user_speech", {
            "call_id": self.call_id,
            "text": user_text,
            "turn": self.turn_count,
        })

        logger.info("用户说: call_id=%s, turn=%d, text=%s", self.call_id, self.turn_count, user_text[:50])

        # 检查是否超过最大轮数
        if self.turn_count >= self.config.max_turns:
            await self._speak(self.config.goodbye_text)
            await self.end("max_turns_reached")
            return

        # 检查通话时长
        if self.started_at and time.time() - self.started_at > self.config.max_call_duration_sec:
            await self._speak("通话时间已到，感谢您的来电，再见。")
            await self.end("max_duration_reached")
            return

        # 调用Agent获取回复
        self._agent_task = asyncio.create_task(self._call_agent(user_text))

    async def _call_agent(self, user_text: str) -> None:
        """调用Agent获取回复"""
        try:
            # 收集完整回复(语音场景下，先获取完整回复再TTS效果更好)
            reply_text, session_id = await self.agent.chat_sync(
                message=user_text,
                user_id=self.user_id,
                session_id=self.session_id,
            )
            if session_id:
                self.session_id = session_id

            reply_text = reply_text.strip()
            if not reply_text:
                reply_text = "抱歉，我没有理解您的意思，能再说一次吗？"

            self._emit_event("agent_reply", {
                "call_id": self.call_id,
                "text": reply_text[:100],
            })

            # 开始播放回复
            await self._speak(reply_text)

        except asyncio.CancelledError:
            logger.debug("Agent调用被取消: call_id=%s", self.call_id)
        except Exception as e:
            logger.error("Agent调用异常: call_id=%s, error=%s", self.call_id, e)
            await self._speak("抱歉，系统暂时无法处理，请稍后再试。")

    async def _speak(self, text: str) -> None:
        """TTS合成并播放语音"""
        self._transition(CallState.SPEAKING)

        try:
            async for audio_chunk in self.tts.synthesize_stream(text):
                if self._should_stop:
                    break

                # 累积到录音缓冲
                if self.config.recording_enabled:
                    self._ai_audio_buffer += audio_chunk

                # 发送音频给FreeSWITCH/客户端
                self._audio_output(audio_chunk)

                # 检查是否被打断
                if self.state != CallState.SPEAKING:
                    break

        except asyncio.CancelledError:
            logger.debug("TTS被取消: call_id=%s", self.call_id)
        except Exception as e:
            logger.error("TTS异常: call_id=%s, error=%s", self.call_id, e)

        # 播放完成，回到LISTENING
        if self.state == CallState.SPEAKING and not self._should_stop:
            self._transition(CallState.LISTENING)
            self.vad.reset()

    async def _on_barge_in(self) -> None:
        """用户打断AI说话"""
        # 停止TTS
        await self.tts.stop()

        # 重置状态
        self.vad.reset()
        await self.stt.start_stream()
        self._stt_buffer = b""

        # 切换到LISTENING
        self._transition(CallState.LISTENING)

        self._emit_event("barge_in", {"call_id": self.call_id})
        logger.info("打断: call_id=%s", self.call_id)

    def _transition(self, new_state: CallState) -> None:
        """状态转换"""
        old_state = self.state
        self.state = new_state
        logger.debug("状态转换: %s → %s (call_id=%s)", old_state.value, new_state.value, self.call_id)

    async def _save_recording(self) -> None:
        """保存通话录音"""
        try:
            import os
            os.makedirs(self.config.recording_dir, exist_ok=True)
            filepath = os.path.join(self.config.recording_dir, f"{self.call_id}.wav")
            wav_data = pcm_to_wav(self._user_audio_buffer, self.config.sample_rate)
            with open(filepath, "wb") as f:
                f.write(wav_data)
            logger.info("录音保存: %s", filepath)
        except Exception as e:
            logger.warning("保存录音失败: %s", e)

    def _emit_event(self, event_type: str, data: dict) -> None:
        """发送事件通知"""
        if self._event_callback:
            try:
                self._event_callback(event_type, data)
            except Exception as e:
                logger.warning("事件回调异常: %s", e)
