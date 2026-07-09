"""语音网关配置"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class VoiceConfig:
    """语音网关配置"""

    # === 服务器配置 ===
    host: str = "0.0.0.0"
    port: int = 8765  # WebSocket端口
    max_concurrent_calls: int = 50  # 最大并发通话数

    # === 音频参数 ===
    sample_rate: int = 16000  # 采样率 (16kHz, FunASR标准)
    phone_sample_rate: int = 8000  # 电话线路采样率 (8kHz)
    frame_duration_ms: int = 30  # 每帧时长(ms), VAD使用
    channels: int = 1  # 单声道

    # === STT配置 ===
    stt_engine: Literal["funasr", "dummy"] = "funasr"
    funasr_model: str = "paraformer-zh-streaming"  # FunASR流式模型
    funasr_model_revision: str = "v2.0.4"
    stt_language: str = "zh"

    # === TTS配置 ===
    tts_engine: Literal["cosyvoice", "edge_tts", "dummy"] = "edge_tts"
    edge_tts_voice: str = "zh-CN-XiaoxiaoNeural"  # Edge-TTS中文女声
    edge_tts_rate: str = "+0%"  # 语速调整
    cosyvoice_model_dir: str = ""  # CosyVoice模型路径(需提前下载)

    # === VAD配置 ===
    vad_engine: Literal["silero", "webrtc"] = "silero"
    vad_threshold: float = 0.5  # VAD触发阈值
    vad_silence_ms: int = 600  # 静音多少ms认为说完
    vad_speech_ms: int = 300  # 语音多少ms认为开始说话

    # === Agent配置 ===
    agent_api_url: str = "http://localhost:8000"  # 现有Agent API地址
    agent_user_id_prefix: str = "phone_"  # 电话用户ID前缀

    # === FreeSWITCH配置 ===
    freeswitch_host: str = "127.0.0.1"
    freeswitch_esl_port: int = 8021
    freeswitch_esl_password: str = "ClueCon"

    # === 通话管理 ===
    max_call_duration_sec: int = 600  # 最多10分钟
    welcome_text: str = "您好，欢迎使用智能优选客服，请问有什么可以帮您？"
    goodbye_text: str = "感谢您的来电，祝您生活愉快，再见！"
    silence_timeout_sec: int = 15  # 用户沉默超时
    max_turns: int = 50  # 最多对话轮数

    # === 录音 ===
    recording_enabled: bool = True
    recording_dir: str = "recordings"
