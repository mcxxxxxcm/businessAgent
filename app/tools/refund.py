"""退货退款 + 售后工单工具"""

import uuid

from langchain_core.tools import tool

# === 模拟数据 ===
_MOCK_REFUNDS = {}


@tool
async def create_refund(
    order_id: str,
    reason: str,
    refund_type: str = "return_refund",
    items: list[str] | None = None,
) -> dict:
    """创建退货退款申请。

    Args:
        order_id: 订单编号
        reason: 退款原因
        refund_type: 退款类型: return_refund(退货退款) / exchange(换货) / refund_only(仅退款)
        items: 涉及的商品ID列表(可选)

    Returns:
        退款申请信息
    """
    refund_id = f"RF{uuid.uuid4().hex[:10]}"

    refund_type_map = {
        "return_refund": "退货退款",
        "exchange": "换货",
        "refund_only": "仅退款",
    }

    result = {
        "refund_id": refund_id,
        "order_id": order_id,
        "status": "pending_review",
        "status_cn": "待审核",
        "refund_type": refund_type,
        "refund_type_cn": refund_type_map.get(refund_type, refund_type),
        "reason": reason,
        "estimated_processing_days": 3,
        "message": "退款申请已提交，我们将在1-2个工作日内审核。审核通过后退款将原路返回。",
    }

    _MOCK_REFUNDS[refund_id] = result
    return result


@tool
async def create_service_ticket(
    order_id: str,
    issue_type: str,
    description: str,
    priority: str = "normal",
) -> dict:
    """创建售后工单。

    Args:
        order_id: 订单编号
        issue_type: 问题类型: quality(质量问题) / damaged(商品损坏) / wrong_item(错发漏发) / other
        description: 问题描述
        priority: 优先级: low / normal / high / urgent

    Returns:
        工单信息
    """
    ticket_id = f"TK{uuid.uuid4().hex[:10]}"

    issue_type_map = {
        "quality": "质量问题",
        "damaged": "商品损坏",
        "wrong_item": "错发漏发",
        "other": "其他",
    }

    estimated_hours = 4 if priority in ("high", "urgent") else 24

    return {
        "ticket_id": ticket_id,
        "order_id": order_id,
        "status": "open",
        "status_cn": "处理中",
        "issue_type": issue_type,
        "issue_type_cn": issue_type_map.get(issue_type, issue_type),
        "priority": priority,
        "estimated_response_hours": estimated_hours,
        "message": f"售后工单已创建(工单号: {ticket_id})，客服专员将在 {estimated_hours} 小时内联系您。",
    }


@tool
async def query_refund_status(refund_id: str) -> dict:
    """查询退款进度。

    Args:
        refund_id: 退款单号

    Returns:
        退款进度信息
    """
    # 先查模拟数据
    if refund_id in _MOCK_REFUNDS:
        record = _MOCK_REFUNDS[refund_id]
        return {
            **record,
            "timeline": [
                {"time": "刚刚", "action": "退款申请已提交"},
            ],
        }

    # 兜底模拟数据
    mock_status = {
        "RF1234567890": {
            "refund_id": "RF1234567890",
            "order_id": "ORD20250101001",
            "status": "processing",
            "status_cn": "退款中",
            "amount": 299.00,
            "timeline": [
                {"time": "2025-01-02 10:05", "action": "退款处理中，预计1-3个工作日到账"},
                {"time": "2025-01-02 10:00", "action": "退款申请已审核通过"},
            ],
        }
    }

    status = mock_status.get(refund_id)
    if not status:
        return {"error": f"退款单 {refund_id} 不存在，请检查退款单号是否正确"}
    return status
