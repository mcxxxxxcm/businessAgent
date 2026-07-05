"""商品搜索 + 库存查询工具"""

from langchain_core.tools import tool


# === 模拟商品数据 ===
_MOCK_PRODUCTS = [
    {
        "product_id": "P001",
        "name": "无线蓝牙耳机 Pro",
        "category": "electronics",
        "price": 299.00,
        "rating": 4.8,
        "description": "高品质降噪蓝牙耳机，续航30小时，支持快充",
        "brand": "音享",
    },
    {
        "product_id": "P002",
        "name": "蓝牙耳机收纳盒",
        "category": "accessories",
        "price": 49.00,
        "rating": 4.5,
        "description": "硬壳保护盒，适配主流蓝牙耳机",
        "brand": "护品",
    },
    {
        "product_id": "P003",
        "name": "智能手环 Lite",
        "category": "electronics",
        "price": 199.00,
        "rating": 4.6,
        "description": "心率监测、睡眠追踪、50米防水",
        "brand": "动量",
    },
    {
        "product_id": "P004",
        "name": "机械键盘 RGB",
        "category": "electronics",
        "price": 459.00,
        "rating": 4.7,
        "description": "Cherry轴体，全键无冲，RGB背光",
        "brand": "键道",
    },
    {
        "product_id": "P005",
        "name": "手机保护壳 透明款",
        "category": "accessories",
        "price": 39.90,
        "rating": 4.3,
        "description": "TPU材质，防摔防黄，多机型适配",
        "brand": "护品",
    },
]

_MOCK_INVENTORY = {
    "P001": {
        "product_id": "P001",
        "total_available": 156,
        "warehouses": [
            {"name": "华南仓", "available": 89},
            {"name": "华东仓", "available": 67},
        ],
        "restock_date": None,
    },
    "P002": {
        "product_id": "P002",
        "total_available": 0,
        "warehouses": [
            {"name": "华南仓", "available": 0},
            {"name": "华东仓", "available": 0},
        ],
        "restock_date": "2025-01-15",
    },
    "P003": {
        "product_id": "P003",
        "total_available": 42,
        "warehouses": [
            {"name": "华南仓", "available": 28},
            {"name": "华东仓", "available": 14},
        ],
        "restock_date": None,
    },
    "P004": {
        "product_id": "P004",
        "total_available": 7,
        "warehouses": [
            {"name": "华南仓", "available": 3},
            {"name": "华东仓", "available": 4},
        ],
        "restock_date": None,
    },
    "P005": {
        "product_id": "P005",
        "total_available": 230,
        "warehouses": [
            {"name": "华南仓", "available": 130},
            {"name": "华东仓", "available": 100},
        ],
        "restock_date": None,
    },
}


@tool
async def search_products(
    keyword: str,
    category: str | None = None,
    price_max: float | None = None,
) -> list[dict]:
    """搜索商品。

    Args:
        keyword: 搜索关键词
        category: 商品分类(可选): electronics / accessories / clothing / home
        price_max: 最高价格(可选)

    Returns:
        匹配的商品列表
    """
    results = []
    for p in _MOCK_PRODUCTS:
        # 关键词匹配(名称或描述)
        if keyword.lower() not in p["name"].lower() and keyword.lower() not in p["description"].lower():
            continue
        if category and p["category"] != category:
            continue
        if price_max and p["price"] > price_max:
            continue
        results.append(p)

    if not results:
        return [{"message": f"未找到与 '{keyword}' 相关的商品，请尝试其他关键词"}]
    return results


@tool
async def check_inventory(product_id: str) -> dict:
    """查询商品库存。

    Args:
        product_id: 商品ID，如 "P001"

    Returns:
        库存信息，包含各仓库可用数量
    """
    inventory = _MOCK_INVENTORY.get(product_id)
    if not inventory:
        return {"error": f"商品 {product_id} 不存在"}
    return inventory
