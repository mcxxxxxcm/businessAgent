"""商品数据服务(模拟) - 生产环境替换为真实数据库/API调用"""

from app.tools.product_search import search_products, check_inventory

__all__ = ["search_products", "check_inventory"]
