"""完整启动验证 — 测试 get_graph() 和服务启动"""

import sys
import os
import traceback
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime

# 设置 sys.path
sys.path.insert(0, r"D:\Agent\智能客服")

# Windows 事件循环策略
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

RESULT_FILE = r"D:\Agent\智能客服\scripts\test_result.txt"
PYTHON = r"D:\Agent\software\envs\cs_agent\python.exe"


def log(msg):
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        f.write(f"[{ts}] {msg}\n")
        f.flush()


async def test_get_graph():
    """直接测试 get_graph()"""
    log("=== 测试1: get_graph() 异步初始化 ===")
    try:
        from app.api.deps import get_graph
        log("  get_graph 已导入")

        graph = await asyncio.wait_for(get_graph(), timeout=60)
        log(f"  get_graph() OK: {type(graph).__name__}")

        if hasattr(graph, 'nodes'):
            log(f"  graph.nodes = {list(graph.nodes.keys())}")

        # 单例验证
        graph2 = await get_graph()
        if graph2 is graph:
            log("  单例验证: 同一对象 ✓")
        else:
            log("  单例验证: 不同对象 (可能之前已缓存)")

        log("=== 测试1: 通过 ===\n")
        return True

    except asyncio.TimeoutError:
        log("  FAILED - get_graph() 超时(60秒)")
        return False
    except Exception as e:
        log(f"  FAILED - {type(e).__name__}: {e}")
        log(f"  {traceback.format_exc()}")
        return False


def test_uvicorn():
    """子进程启动 uvicorn，然后 HTTP 测试"""
    log("=== 测试2: uvicorn 服务启动 ===")

    env = os.environ.copy()
    env["PYTHONPATH"] = r"D:\Agent\智能客服"

    log("  启动 uvicorn 子进程...")
    proc = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # 等待服务启动 (最多60秒)
    log("  等待服务就绪 (最多60秒)...")
    start = time.time()
    max_wait = 60
    server_ready = False

    while time.time() - start < max_wait:
        # 检查进程是否已退出
        ret = proc.poll()
        if ret is not None:
            stdout_data = proc.stdout.read()
            log(f"  uvicorn 进程已退出, code={ret}")
            log(f"  输出:\n{stdout_data[-2000:]}")
            break

        # 尝试 HTTP 请求
        try:
            req = urllib.request.Request("http://localhost:8000/", method="GET")
            resp = urllib.request.urlopen(req, timeout=3)
            log(f"  HTTP GET / → {resp.status}")
            server_ready = True
            break
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            time.sleep(2)

    if server_ready:
        log("  服务启动成功！")

        # 测试 health endpoint
        try:
            req = urllib.request.Request("http://localhost:8000/health", method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            body = resp.read().decode()
            log(f"  GET /health → {resp.status}: {body[:200]}")
        except Exception as e:
            log(f"  GET /health 失败: {e}")

        log("=== 测试2: 通过 ===\n")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True
    else:
        elapsed = time.time() - start
        log(f"  服务未能在{max_wait}秒内就绪 (已等{elapsed:.0f}秒)")
        # 读取子进程输出
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        log("=== 测试2: 失败 ===\n")
        return False


async def async_main():
    # 测试1: get_graph()
    result1 = await test_get_graph()

    # 测试2: uvicorn 启动 (在子进程中进行，避免事件循环冲突)
    if result1:
        result2 = test_uvicorn()
    else:
        log("跳过服务启动测试 (get_graph 失败)")
        result2 = False

    # 汇总
    log("=" * 50)
    if result1 and result2:
        log("所有测试通过! ✓")
    elif result1:
        log("get_graph() 通过, 但服务启动失败")
    else:
        log("get_graph() 失败, 服务启动跳过")


if __name__ == "__main__":
    # 清空结果文件
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== test_startup.py 开始 {datetime.now().isoformat()} ===\n")

    log(f"Python: {sys.version}")
    log(f"Event loop policy: {asyncio.get_event_loop_policy().__class__.__name__}")

    try:
        asyncio.run(async_main())
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}")
        log(f"{traceback.format_exc()}")
