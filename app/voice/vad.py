"""VAD (Voice Activity Detection) 语音端点检测

检测用户何时开始说话、何时说完，用于:
1. 判断用户一句话说完 → 触发Agent推理
2. 检测打断 → 用户说话时AI停止播放

支持两种引擎:
- silero: Silero VAD模型, 精度高, 需下载模型(~2MB)
- webrtc: WebRTC VAD, 轻量无需模型, 精度稍低
"""

import logging
from enum import Enum
from typing import Optional

import numpy as np

from app.voice.audio import split_audio_frames

logger = logging.getLogger(__name__)


class VoiceState(str, Enum):
    """语音状态"""
    SILENCE = "silence"  # 静音
    SPEECH = "speech"    # 说话中
    END_OF_SPEECH = "end_of_speech"  # 说完了一句


class VADResult:
    """VAD检测结果"""

    def __init__(self, state: VoiceState, is_speech: bool, probability: float = 0.0):
        self.state = state
        self.is_speech = is_speech
        self.probability = probability

    def __repr__(self):
        return f"VADResult(state={self.state.value}, is_speech={self.is_speech}, prob={self.probability:.2f})"


class BaseVAD:
    """VAD基类"""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        silence_ms: int = 600,
        speech_ms: int = 300,
        threshold: float = 0.5,
    ):
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.silence_ms = silence_ms
        self.speech_ms = speech_ms
        self.threshold = threshold

        # 状态追踪
        self._is_speaking = False
        self._speech_frames = 0  # 连续语音帧数
        self._silence_frames = 0  # 连续静音帧数
        self._frames_per_speech = int(speech_ms / frame_duration_ms)
        self._frames_per_silence = int(silence_ms / frame_duration_ms)

    def reset(self):
        """重置状态"""
        self._is_speaking = False
        self._speech_frames = 0
        self._silence_frames = 0

    def process_frame(self, audio_frame: bytes) -> VADResult:
        """处理一帧音频

        Args:
            audio_frame: PCM 16bit 单帧音频数据

        Returns:
            VADResult: 检测结果
        """
        is_speech, prob = self._detect_speech(audio_frame)

        if is_speech:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            self._speech_frames = 0

        # 状态转换
        if not self._is_speaking:
            # 当前静音 → 检测是否开始说话
            if self._speech_frames >= self._frames_per_speech:
                self._is_speaking = True
                return VADResult(VoiceState.SPEECH, True, prob)
            return VADResult(VoiceState.SILENCE, False, prob)
        else:
            # 当前说话中 → 检测是否说完
            if self._silence_frames >= self._frames_per_silence:
                self._is_speaking = False
                return VADResult(VoiceState.END_OF_SPEECH, False, prob)
            return VADResult(VoiceState.SPEECH, True, prob)

    def process_audio(self, audio_data: bytes) -> list[VADResult]:
        """处理一段音频(自动切帧)

        Returns:
            所有帧的VAD结果列表
        """
        frames = split_audio_frames(audio_data, self.frame_duration_ms, self.sample_rate)
        return [self.process_frame(frame) for frame in frames]

    def is_currently_speaking(self) -> bool:
        """当前是否正在说话(用于打断检测)"""
        return self._is_speaking

    def _detect_speech(self, audio_frame: bytes) -> tuple[bool, float]:
        """检测单帧是否包含语音(子类实现)

        Returns:
            (is_speech, probability)
        """
        raise NotImplementedError


class SileroVAD(BaseVAD):
    """Silero VAD - 基于深度学习的VAD模型

    精度高，支持流式检测，模型仅~2MB。
    首次使用会自动下载模型。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._model = None
        self._model_initialized = False

    def _init_model(self):
        """懒加载Silero模型"""
        if self._model_initialized:
            return

        try:
            import torch
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                trust_repo=True,
            )
            self._model = model
            self._model_initialized = True
            logger.info("Silero VAD模型加载成功")
        except Exception as e:
            logger.error("Silero VAD模型加载失败: %s, 降级到能量检测VAD", e)
            self._model = None
            self._model_initialized = True  # 标记已尝试，不再重复

    def _detect_speech(self, audio_frame: bytes) -> tuple[bool, float]:
        self._init_model()

        if self._model is None:
            # 降级到简单能量检测
            return self._fallback_energy_vad(audio_frame)

        try:
            import torch

            # PCM bytes → float32 tensor
            audio_int16 = np.frombuffer(audio_frame, dtype=np.int16)
            audio_float32 = audio_int16.astype(np.float32) / 32768.0
            audio_tensor = torch.from_numpy(audio_float32)

            prob = self._model(audio_tensor, self.sample_rate).item()
            is_speech = prob > self.threshold

            return is_speech, prob
        except Exception as e:
            logger.warning("Silero VAD检测失败: %s", e)
            return self._fallback_energy_vad(audio_frame)

    def _fallback_energy_vad(self, audio_frame: bytes) -> tuple[bool, float]:
        """简单能量检测VAD(降级方案)"""
        from app.voice.audio import calculate_rms

        rms = calculate_rms(audio_frame)
        energy_threshold = 0.015
        is_speech = rms > energy_threshold
        return is_speech, rms


class WebRTCVADEngine(BaseVAD):
    """WebRTC VAD - 轻量级VAD

    无需模型下载，CPU占用极低，但精度不如Silero。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._vad = None

    def _init_vad(self):
        if self._vad is not None:
            return

        try:
            import webrtcvad
            aggressiveness = 3  # 0-3, 3最激进过滤
            self._vad = webrtcvad.Vad(aggressiveness)
            logger.info("WebRTC VAD初始化成功")
        except ImportError:
            logger.error("webrtcvad未安装, 降级到能量检测")
            self._vad = None

    def _detect_speech(self, audio_frame: bytes) -> tuple[bool, float]:
        self._init_vad()

        if self._vad is None:
            from app.voice.audio import calculate_rms
            rms = calculate_rms(audio_frame)
            return rms > 0.015, rms

        try:
            is_speech = self._vad.is_speech(audio_frame, self.sample_rate)
            return is_speech, 1.0 if is_speech else 0.0
        except Exception as e:
            logger.warning("WebRTC VAD检测失败: %s", e)
            return False, 0.0


def create_vad(
    engine: str = "silero",
    sample_rate: int = 16000,
    frame_duration_ms: int = 30,
    silence_ms: int = 600,
    speech_ms: int = 300,
    threshold: float = 0.5,
) -> BaseVAD:
    """VAD工厂函数

    Args:
        engine: "silero" 或 "webrtc"
        sample_rate: 采样率
        frame_duration_ms: 帧长
        silence_ms: 判定说完的静音时长
        speech_ms: 判定开始说话的语音时长
        threshold: 语音检测阈值

    Returns:
        VAD实例
    """
    kwargs = dict(
        sample_rate=sample_rate,
        frame_duration_ms=frame_duration_ms,
        silence_ms=silence_ms,
        speech_ms=speech_ms,
        threshold=threshold,
    )

    if engine == "silero":
        return SileroVAD(**kwargs)
    elif engine == "webrtc":
        return WebRTCVADEngine(**kwargs)
    else:
        logger.warning("未知VAD引擎: %s, 使用silero", engine)
        return SileroVAD(**kwargs)
