"""启动脚本 - 使用--reload模式确保Windows事件循环策略正确设置

Python 3.14+下set_event_loop_policy被deprecated且可能不生效，
--reload模式通过spawn子进程重新初始化解释器，确保策略正确应用。
"""
import sys
import os

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["app"],
    )
