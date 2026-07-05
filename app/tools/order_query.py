"""订单查询 + 物流追踪工具"""

from langchain_core.tools import tool


@tool
async def query_order(order_id: str) -> dict:
    """查询订单详情和当前状态。

    Args:
        order_id: 订单编号，如 "ORD20250101001"

    Returns:
        包含订单状态、商品列表、金额等信息的字典
    """
    # === 模拟数据 ===
    mock_orders = {
        "ORD20250101001": {
            "order_id": "ORD20250101001",
            "status": "shipped",
            "items": [
                {"name": "无线蓝牙耳机 Pro", "quantity": 1, "price": 299.00},
                {"name": "手机保护壳", "quantity": 2, "price": 39.90},
            ],
            "total_amount": 378.80,
            "created_at": "2025-01-01 10:30:00",
            "paid_at": "2025-01-01 10:31:00",
            "tracking_number": "SF1234567890",
        },
        "ORD20250101002": {
            "order_id": "ORD20250101002",
            "status": "pending",
            "items": [
                {"name": "智能手环", "quantity": 1, "price": 199.00},
            ],
            "total_amount": 199.00,
            "created_at": "2025-01-02 14:20:00",
            "paid_at": None,
            "tracking_number": None,
        },
        "ORD20250101003": {
            "order_id": "ORD20250101003",
            "status": "delivered",
            "items": [
                {"name": "机械键盘", "quantity": 1, "price": 459.00},
                {"name": "键帽套装", "quantity": 1, "price": 89.00},
            ],
            "total_amount": 548.00,
            "created_at": "2024-12-28 09:15:00",
            "paid_at": "2024-12-28 09:16:00",
            "tracking_number": "YT9876543210",
        },
    }

    order = mock_orders.get(order_id)
    if not order:
        return {"error": f"订单 {order_id} 不存在，请检查订单号是否正确"}

    # 状态映射为中文
    status_map = {
        "pending": "待付款",
        "paid": "已付款",
        "shipped": "已发货",
        "delivered": "已签收",
        "cancelled": "已取消",
    }
    order["status_cn"] = status_map.get(order["status"], order["status"])
    return order


@tool
async def track_logistics(tracking_number: str) -> dict:
    """追踪物流信息。

    Args:
        tracking_number: 物流运单号

    Returns:
        包含物流轨迹、预计到达时间等信息的字典
    """
    mock_logistics = {
        "SF1234567890": {
            "tracking_number": "SF1234567890",
            "carrier": "顺丰速运",
            "status": "in_transit",
            "status_cn": "运输中",
            "estimated_delivery": "2025-01-03",
            "timeline": [
                {"time": "2025-01-02 08:00", "location": "深圳集散中心", "action": "已发出"},
                {"time": "2025-01-02 14:30", "location": "广州中转站", "action": "运输中"},
                {"time": "2025-01-01 16:00", "location": "深圳仓库", "action": "已揽收"},
            ],
        },
        "YT9876543210": {
            "tracking_number": "YT9876543210",
            "carrier": "圆通速递",
            "status": "delivered",
            "status_cn": "已签收",
            "estimated_delivery": None,
            "timeline": [
                {"time": "2024-12-30 10:00", "location": "北京朝阳区", "action": "已签收"},
                {"time": "2024-12-30 08:00", "location": "北京派送站", "action": "派送中"},
                {"time": "2024-12-29 20:00", "location": "北京分拨中心", "action": "已到达"},
            ],
        },
    }

    logistics = mock_logistics.get(tracking_number)
    if not logistics:
        return {"error": f"运单 {tracking_number} 不存在，请检查运单号是否正确"}
    return logistics
