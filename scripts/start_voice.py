"""启动语音网关

用法:
    python scripts/start_voice.py

    # 使用dummy引擎(无需安装FunASR/CosyVoice)
    python scripts/start_voice.py --stt dummy --tts dummy
"""

import asyncio
import sys

# Windows事件循环策略(psycopg兼容)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import argparse
import logging

from app.voice.config import VoiceConfig
from app.voice.gateway import VoiceGateway


def parse_args():
    parser = argparse.ArgumentParser(description="AI电话客服语音网关")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket端口")
    parser.add_argument("--stt", default="funasr", choices=["funasr", "dummy"], help="STT引擎")
    parser.add_argument("--tts", default="edge_tts", choices=["edge_tts", "cosyvoice", "dummy"], help="TTS引擎")
    parser.add_argument("--vad", default="silero", choices=["silero", "webrtc"], help="VAD引擎")
    parser.add_argument("--agent-url", default="http://localhost:8000", help="Agent API地址")
    parser.add_argument("--max-calls", type=int, default=50, help="最大并发通话数")
    return parser.parse_args()


async def run():
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = VoiceConfig(
        host=args.host,
        port=args.port,
        stt_engine=args.stt,
        tts_engine=args.tts,
        vad_engine=args.vad,
        agent_api_url=args.agent_url,
        max_concurrent_calls=args.max_calls,
    )

    gateway = VoiceGateway(config)

    try:
        await gateway.start()
        logger = logging.getLogger(__name__)
        logger.info("=" * 50)
        logger.info("  AI电话客服语音网关")
        logger.info("  WebSocket: ws://%s:%d", config.host, config.port)
        logger.info("  STT: %s | TTS: %s | VAD: %s", config.stt_engine, config.tts_engine, config.vad_engine)
        logger.info("  Agent: %s", config.agent_api_url)
        logger.info("  最大并发: %d", config.max_concurrent_calls)
        logger.info("=" * 50)
        logger.info("按Ctrl+C停止")

        await asyncio.Future()
    except KeyboardInterrupt:
        pass
    finally:
        await gateway.stop()


if __name__ == "__main__":
    asyncio.run(run())
