"""转人工客服工具"""

import uuid

from langchain_core.tools import tool


@tool
async def transfer_to_human(
    reason: str,
    priority: str = "normal",
    summary: str | None = None,
) -> dict:
    """将当前对话转交给人工客服。

    Args:
        reason: 转人工原因
        priority: 优先级: low / normal / high / urgent
        summary: 对话摘要(可选，帮助人工客服快速了解上下文)

    Returns:
        转接信息
    """
    transfer_id = f"TR{uuid.uuid4().hex[:10]}"

    # 模拟队列位置(根据优先级不同)
    queue_position = {"urgent": 1, "high": 2, "normal": 4, "low": 6}.get(priority, 4)
    estimated_wait = {"urgent": 1, "high": 3, "normal": 5, "low": 10}.get(priority, 5)

    return {
        "transfer_id": transfer_id,
        "status": "queued",
        "status_cn": "排队中",
        "queue_position": queue_position,
        "estimated_wait_minutes": estimated_wait,
        "priority": priority,
        "message": (
            f"正在为您转接人工客服，您当前排在第 {queue_position} 位，"
            f"预计等待约 {estimated_wait} 分钟。感谢您的耐心等待！"
        ),
    }
