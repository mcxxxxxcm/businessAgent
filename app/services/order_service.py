"""订单数据服务(模拟) - 生产环境替换为真实数据库/API调用"""

# 模拟数据与tools/order_query.py中的数据一致
# 生产环境中，此服务将从真实数据库或微服务API获取数据

from app.tools.order_query import query_order, track_logistics

__all__ = ["query_order", "track_logistics"]
