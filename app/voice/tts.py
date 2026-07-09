"""TTS (Text-to-Speech) 语音合成

支持:
- Edge-TTS: 微软免费TTS，无需GPU，音质好，延迟低
- CosyVoice: 阿里开源TTS，支持声音克隆，需GPU
- Dummy: 测试用

流式合成流程:
1. 接收文本
2. 按句子切分
3. 流式合成音频chunk
4. 通过回调推送给通话session
"""

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)


class BaseTTS(ABC):
    """TTS基类"""

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """合成完整音频(非流式)

        Args:
            text: 要合成的文本

        Returns:
            PCM 16bit 16kHz 音频字节
        """
        pass

    @abstractmethod
    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式合成音频

        按句子切分，逐句合成，降低首包延迟。

        Args:
            text: 要合成的文本

        Yields:
            PCM 16bit 16kHz 音频chunk
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止当前合成(用于打断)"""
        pass

    def split_sentences(self, text: str) -> list[str]:
        """将文本切分为句子(用于流式合成)

        按中文标点切分，每句独立合成。
        """
        # 按中英文句号、问号、感叹号、逗号、分号切分
        sentences = re.split(r"(?<=[。！？；，.!?;,\n])", text)
        # 过滤空句，保留标点
        result = []
        for s in sentences:
            s = s.strip()
            if s:
                result.append(s)
        # 如果没有切分成功，整段作为一个句子
        if not result and text.strip():
            result = [text.strip()]
        return result


class EdgeTTSEngine(BaseTTS):
    """Edge-TTS - 微软免费TTS接口

    优点: 无需GPU、音质好、多种声音可选、免费
    缺点: 需要网络、依赖微软服务可用性
    依赖: pip install edge-tts
    """

    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        sample_rate: int = 16000,
    ):
        self.voice = voice
        self.rate = rate
        self.sample_rate = sample_rate
        self._should_stop = False

    async def synthesize(self, text: str) -> bytes:
        """合成完整音频"""
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)

            # 收集所有音频chunk
            audio_chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])

            if not audio_chunks:
                return b""

            # MP3 → PCM (edge-tts输出mp3)
            mp3_data = b"".join(audio_chunks)
            pcm_data = self._mp3_to_pcm(mp3_data)
            return pcm_data

        except ImportError:
            logger.error("edge-tts未安装, 请运行: pip install edge-tts")
            return b""
        except Exception as e:
            logger.error("Edge-TTS合成失败: %s", e)
            return b""

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式合成: 按句子切分，逐句输出"""
        self._should_stop = False
        sentences = self.split_sentences(text)

        for sentence in sentences:
            if self._should_stop:
                logger.debug("TTS被中断(打断)")
                break

            pcm = await self.synthesize(sentence)
            if pcm:
                yield pcm

            # 句间短暂停顿(自然感)
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        """打断当前合成"""
        self._should_stop = True

    def _mp3_to_pcm(self, mp3_data: bytes) -> bytes:
        """MP3转PCM 16bit 16kHz"""
        try:
            import subprocess
            import io

            # 使用ffmpeg转换 (需系统安装ffmpeg)
            process = subprocess.Popen(
                [
                    "ffmpeg",
                    "-i", "pipe:0",  # 从stdin读取
                    "-f", "s16le",  # 输出PCM 16bit
                    "-acodec", "pcm_s16le",
                    "-ar", str(self.sample_rate),  # 采样率
                    "-ac", "1",  # 单声道
                    "-loglevel", "error",
                    "pipe:1",  # 输出到stdout
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stdout, stderr = process.communicate(input=mp3_data, timeout=10)
            return stdout

        except FileNotFoundError:
            # ffmpeg不可用，尝试用pydub
            return self._mp3_to_pcm_pydub(mp3_data)
        except Exception as e:
            logger.warning("MP3转PCM失败: %s", e)
            return b""

    def _mp3_to_pcm_pydub(self, mp3_data: bytes) -> bytes:
        """用pydub做MP3→PCM转换(备选方案)"""
        try:
            from pydub import AudioSegment
            from io import BytesIO

            audio = AudioSegment.from_mp3(BytesIO(mp3_data))
            audio = audio.set_frame_rate(self.sample_rate).set_channels(1).set_sample_width(2)
            return audio.raw_data
        except ImportError:
            logger.error("pydub未安装，无法转换MP3→PCM")
            return b""
        except Exception as e:
            logger.warning("pydub MP3转PCM失败: %s", e)
            return b""


class CosyVoiceEngine(BaseTTS):
    """CosyVoice - 阿里开源TTS引擎

    优点: 音质最好、支持声音克隆、可本地部署
    缺点: 需要GPU、模型较大
    依赖: 需要单独安装cosyvoice
    """

    def __init__(self, model_dir: str = "", sample_rate: int = 22050):
        self.model_dir = model_dir
        self.sample_rate = sample_rate
        self._model = None
        self._should_stop = False

    def _init_model(self):
        """懒加载CosyVoice模型"""
        if self._model is not None:
            return

        try:
            from cosyvoice.cli.cosyvoice import CosyVoice

            self._model = CosyVoice(self.model_dir)
            logger.info("CosyVoice模型加载成功: %s", self.model_dir)
        except ImportError:
            logger.error("cosyvoice未安装, 请参考: https://github.com/FunAudioLLM/CosyVoice")
            raise
        except Exception as e:
            logger.error("CosyVoice模型加载失败: %s", e)
            raise

    async def synthesize(self, text: str) -> bytes:
        """合成完整音频"""
        self._init_model()

        if self._model is None:
            return b""

        try:
            # CosyVoice输出是流式的，收集所有chunk
            audio_chunks = []
            for result in self._model.inference_sft(text, "中文女"):
                audio_np = result["tts_speech"]
                # float32 → int16 PCM
                audio_int16 = (audio_np.numpy() * 32768).astype(np.int16)
                audio_chunks.append(audio_int16.tobytes())

            return b"".join(audio_chunks)
        except Exception as e:
            logger.error("CosyVoice合成失败: %s", e)
            return b""

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式合成"""
        self._should_stop = False
        self._init_model()

        if self._model is None:
            return

        sentences = self.split_sentences(text)
        for sentence in sentences:
            if self._should_stop:
                break

            try:
                for result in self._model.inference_sft(sentence, "中文女"):
                    if self._should_stop:
                        break
                    audio_np = result["tts_speech"]
                    audio_int16 = (audio_np.numpy() * 32768).astype(np.int16)
                    yield audio_int16.tobytes()
            except Exception as e:
                logger.warning("CosyVoice句子合成失败: %s", e)

            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        self._should_stop = True


class DummyTTS(BaseTTS):
    """测试用TTS - 不做实际合成"""

    async def synthesize(self, text: str) -> bytes:
        # 生成1秒静音(16kHz 16bit mono = 32000 bytes)
        return b"\x00" * 32000

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        # 生成0.5秒静音chunk
        yield b"\x00" * 16000

    async def stop(self) -> None:
        pass


def create_tts(
    engine: str = "edge_tts",
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+0%",
    sample_rate: int = 16000,
    cosyvoice_model_dir: str = "",
) -> BaseTTS:
    """TTS工厂函数"""
    if engine == "edge_tts":
        return EdgeTTSEngine(voice=voice, rate=rate, sample_rate=sample_rate)
    elif engine == "cosyvoice":
        return CosyVoiceEngine(model_dir=cosyvoice_model_dir, sample_rate=sample_rate)
    elif engine == "dummy":
        return DummyTTS()
    else:
        logger.warning("未知TTS引擎: %s, 使用edge_tts", engine)
        return EdgeTTSEngine(voice=voice, rate=rate, sample_rate=sample_rate)
