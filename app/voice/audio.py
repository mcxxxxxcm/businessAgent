"""音频处理工具 - 格式转换、重采样、音量检测"""

import io
import struct
import logging
import audioop
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def resample(audio_data: bytes, orig_rate: int, target_rate: int) -> bytes:
    """重采样音频数据

    Args:
        audio_data: PCM 16bit 音频字节
        orig_rate: 原始采样率
        target_rate: 目标采样率

    Returns:
        重采样后的PCM 16bit音频字节
    """
    if orig_rate == target_rate:
        return audio_data

    try:
        # audioop.ratecv: (converted, state) = ratecv(fragment, width, nchannels, inrate, outrate, state)
        converted, _ = audioop.ratecv(audio_data, 2, 1, orig_rate, target_rate, None)
        return converted
    except Exception as e:
        logger.warning("重采样失败: %s", e)
        return audio_data


def calculate_rms(audio_data: bytes) -> float:
    """计算音频RMS(均方根)能量值

    用于检测用户是否在说话(简单的音量检测)。
    """
    if len(audio_data) < 2:
        return 0.0

    try:
        rms = audioop.rms(audio_data, 2)
        # 归一化到0-1范围 (16bit音频最大值32768)
        return min(rms / 32768.0, 1.0)
    except Exception:
        return 0.0


def calculate_rms_numpy(audio_data: bytes) -> float:
    """用numpy计算RMS(更精确)"""
    if len(audio_data) < 2:
        return 0.0

    samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
    rms = np.sqrt(np.mean(samples**2)) / 32768.0
    return min(rms, 1.0)


def is_silence(audio_data: bytes, threshold: float = 0.01) -> bool:
    """检测音频是否为静音

    Args:
        audio_data: PCM 16bit 音频字节
        threshold: RMS阈值, 低于此值视为静音
    """
    return calculate_rms(audio_data) < threshold


def mulaw_encode(audio_data: bytes) -> bytes:
    """PCM线性编码转μ-law编码 (电话线路标准)"""
    try:
        return audioop.lin2ulaw(audio_data, 2)
    except Exception as e:
        logger.warning("μ-law编码失败: %s", e)
        return audio_data


def mulaw_decode(audio_data: bytes) -> bytes:
    """μ-law编码转PCM线性编码"""
    try:
        return audioop.ulaw2lin(audio_data, 2)
    except Exception as e:
        logger.warning("μ-law解码失败: %s", e)
        return audio_data


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """PCM数据转WAV格式 (用于保存录音)"""
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)

    return buffer.getvalue()


def wav_to_pcm(wav_data: bytes) -> tuple[bytes, int]:
    """WAV格式转PCM数据

    Returns:
        (pcm_data, sample_rate)
    """
    import wave

    buffer = io.BytesIO(wav_data)
    with wave.open(buffer, "rb") as wf:
        sample_rate = wf.getframerate()
        pcm_data = wf.readframes(wf.getnframes())

    return pcm_data, sample_rate


def split_audio_frames(
    audio_data: bytes,
    frame_duration_ms: int,
    sample_rate: int = 16000,
) -> list[bytes]:
    """将音频数据按帧长切分

    Args:
        audio_data: PCM 16bit 音频字节
        frame_duration_ms: 每帧时长(ms)
        sample_rate: 采样率

    Returns:
        帧列表
    """
    bytes_per_frame = int(sample_rate * frame_duration_ms / 1000) * 2  # 16bit=2bytes
    frames = []
    for i in range(0, len(audio_data), bytes_per_frame):
        frame = audio_data[i : i + bytes_per_frame]
        if len(frame) == bytes_per_frame:
            frames.append(frame)
    return frames
