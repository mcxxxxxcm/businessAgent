"""STT (Speech-to-Text) 语音识别

支持:
- FunASR: 阿里开源，中文流式识别效果最好
- Dummy: 测试用，直接返回文本

流式识别流程:
1. 不断接收音频chunk
2. FunASR流式模式：每收到chunk返回部分识别结果
3. 当VAD检测到END_OF_SPEECH时，调用finalize()获取完整结果
"""

import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


class BaseSTT(ABC):
    """STT基类"""

    @abstractmethod
    async def start_stream(self) -> None:
        """开始一次流式识别会话"""
        pass

    @abstractmethod
    async def feed_audio(self, audio_chunk: bytes) -> Optional[str]:
        """喂入音频数据，返回部分识别结果(流式)

        Args:
            audio_chunk: PCM 16bit 16kHz 音频字节

        Returns:
            部分识别文本(中间结果)，如果没有则返回None
        """
        pass

    @abstractmethod
    async def finalize(self) -> str:
        """结束流式识别，返回最终完整文本

        当VAD检测到用户说完时调用。
        """
        pass

    @abstractmethod
    async def stop_stream(self) -> None:
        """停止识别会话，释放资源"""
        pass


class FunASRStreamSTT(BaseSTT):
    """FunASR流式语音识别

    使用paraformer-zh-streaming模型，支持实时流式识别。
    中文识别精度极高，支持标点恢复。

    依赖: pip install funasr
    """

    def __init__(
        self,
        model: str = "paraformer-zh-streaming",
        model_revision: str = "v2.0.4",
        sample_rate: int = 16000,
    ):
        self.model_name = model
        self.model_revision = model_revision
        self.sample_rate = sample_rate
        self._model = None
        self._chunk_size = [5, 10, 5]  # 流式chunk配置 [左context, chunk大小, 右context]
        self._encoder_chunk_look_back = 4
        self._decoder_chunk_look_back = 1
        self._chunk_offset = 0
        self._cache = {}
        self._initialized = False

    def _init_model(self):
        """懒加载FunASR模型"""
        if self._initialized:
            return

        try:
            from funasr import AutoModel

            self._model = AutoModel(
                model=self.model_name,
                model_revision=self.model_revision,
                disable_update=True,
            )
            self._initialized = True
            logger.info("FunASR模型加载成功: %s", self.model_name)
        except ImportError:
            logger.error("funasr未安装, 请运行: pip install funasr")
            raise
        except Exception as e:
            logger.error("FunASR模型加载失败: %s", e)
            raise

    async def start_stream(self) -> None:
        """开始流式识别会话"""
        self._init_model()
        self._cache = {}
        self._chunk_offset = 0
        logger.debug("FunASR流式识别会话开始")

    async def feed_audio(self, audio_chunk: bytes) -> Optional[str]:
        """喂入音频chunk，返回中间识别结果"""
        if self._model is None:
            return None

        try:
            import numpy as np

            # PCM bytes → numpy array
            audio_np = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0

            result = self._model.generate(
                input=audio_np,
                cache=self._cache,
                is_final=False,
                chunk_size=self._chunk_size,
                encoder_chunk_look_back=self._encoder_chunk_look_back,
                decoder_chunk_look_back=self._decoder_chunk_look_back,
            )

            if result and len(result) > 0:
                text = result[0].get("text", "")
                if text:
                    return text

        except Exception as e:
            logger.warning("FunASR流式识别失败: %s", e)

        return None

    async def finalize(self) -> str:
        """结束流式，获取最终完整文本"""
        if self._model is None:
            return ""

        try:
            # 喂入空的final chunk
            result = self._model.generate(
                input=np.zeros(1, dtype=np.float32),
                cache=self._cache,
                is_final=True,
                chunk_size=self._chunk_size,
                encoder_chunk_look_back=self._encoder_chunk_look_back,
                decoder_chunk_look_back=self._decoder_chunk_look_back,
            )

            if result and len(result) > 0:
                return result[0].get("text", "")

        except Exception as e:
            logger.warning("FunASR finalize失败: %s", e)

        return ""

    async def stop_stream(self) -> None:
        """停止识别会话"""
        self._cache = {}
        self._chunk_offset = 0


class DummySTT(BaseSTT):
    """测试用STT - 不做实际识别，用于开发调试"""

    def __init__(self, **kwargs):
        self._audio_buffer = b""
        self._text_buffer = ""

    async def start_stream(self) -> None:
        self._audio_buffer = b""
        self._text_buffer = ""

    async def feed_audio(self, audio_chunk: bytes) -> Optional[str]:
        self._audio_buffer += audio_chunk
        return None

    async def finalize(self) -> str:
        # 返回缓冲区大小作为模拟结果
        return f"[模拟识别: 收到{len(self._audio_buffer)}字节音频]"

    async def stop_stream(self) -> None:
        self._audio_buffer = b""


def create_stt(
    engine: str = "funasr",
    model: str = "paraformer-zh-streaming",
    model_revision: str = "v2.0.4",
    sample_rate: int = 16000,
) -> BaseSTT:
    """STT工厂函数"""
    if engine == "funasr":
        return FunASRStreamSTT(
            model=model,
            model_revision=model_revision,
            sample_rate=sample_rate,
        )
    elif engine == "dummy":
        return DummySTT()
    else:
        logger.warning("未知STT引擎: %s, 使用dummy", engine)
        return DummySTT()
