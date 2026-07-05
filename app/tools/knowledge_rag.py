"""知识库RAG检索工具"""

from langchain_core.tools import tool

# === 模拟知识库 ===
_MOCK_KNOWLEDGE = [
    {
        "title": "7天无理由退货政策",
        "category": "return_policy",
        "content": (
            "自签收之日起7天内，商品保持完好可申请无理由退货。"
            "以下商品除外：定制商品、生鲜食品、已拆封的音像制品、数字化商品、贴身衣物。"
            "退货运费由买家承担，退款将在收到退货商品后3个工作日内原路返回。"
        ),
    },
    {
        "title": "质量问题退换货",
        "category": "return_policy",
        "content": (
            "商品存在质量问题，可在签收后15天内申请退换货。"
            "需提供商品问题照片或视频作为凭证。"
            "质量问题退换货运费由卖家承担，审核通过后1-2个工作日处理。"
        ),
    },
    {
        "title": "电子产品保修条款",
        "category": "warranty",
        "content": (
            "电子产品自购买之日起享受1年保修服务。"
            "保修期内，非人为损坏可免费维修或更换。"
            "需提供购买凭证(订单截图或发票)。"
            "以下情况不在保修范围内：进水、摔损、私自拆修、超过保修期。"
        ),
    },
    {
        "title": "支付方式说明",
        "category": "payment",
        "content": (
            "支持以下支付方式：支付宝、微信支付、银行卡、信用卡、花呗。"
            "订单支付成功后不可更改支付方式。"
            "退款将原路返回，信用卡退款可能需要5-10个工作日。"
        ),
    },
    {
        "title": "配送时效说明",
        "category": "shipping",
        "content": (
            "普通配送：2-5个工作日送达。"
            "加急配送：1-2个工作日送达(仅限部分城市)。"
            "偏远地区可能延长1-3个工作日。"
            "大促期间(618/双11)配送时效可能延长1-2天。"
        ),
    },
    {
        "title": "优惠券使用规则",
        "category": "promotion",
        "content": (
            "优惠券可在结算时使用，每笔订单仅限使用1张。"
            "部分商品不支持优惠券(如特价商品、预售商品)。"
            "优惠券不可叠加使用，不可兑换现金。"
            "过期优惠券自动失效，不予补发。"
        ),
    },
]


@tool
async def search_knowledge_base(query: str, category: str | None = None) -> list[dict]:
    """检索知识库，获取退换货政策、保修条款、商品FAQ等信息。

    Args:
        query: 用户问题或检索关键词
        category: 知识分类(可选): return_policy / warranty / product_faq / shipping / payment / promotion

    Returns:
        相关知识条目列表
    """
    # 简单关键词匹配模拟(生产环境替换为向量检索)
    results = []
    query_lower = query.lower()

    # 关键词映射
    keyword_map = {
        "退货": "return_policy",
        "退款": "return_policy",
        "换货": "return_policy",
        "保修": "warranty",
        "维修": "warranty",
        "质量": "return_policy",
        "支付": "payment",
        "付款": "payment",
        "配送": "shipping",
        "快递": "shipping",
        "物流": "shipping",
        "运费": "shipping",
        "优惠券": "promotion",
        "折扣": "promotion",
        "活动": "promotion",
    }

    # 确定目标分类
    target_categories = set()
    if category:
        target_categories.add(category)
    else:
        for kw, cat in keyword_map.items():
            if kw in query_lower:
                target_categories.add(cat)

    # 如果没匹配到关键词，返回全部
    if not target_categories:
        target_categories = {"return_policy", "warranty", "shipping", "payment", "promotion"}

    for item in _MOCK_KNOWLEDGE:
        if item["category"] in target_categories:
            # 简单的相关性评分
            score = 0.5
            for word in query_lower:
                if word in item["content"].lower() or word in item["title"].lower():
                    score += 0.1
            results.append({**item, "relevance_score": min(score, 1.0)})

    # 按相关性排序
    results.sort(key=lambda x: x["relevance_score"], reverse=True)

    if not results:
        return [{"title": "未找到相关内容", "content": "未在知识库中找到与您问题相关的内容，建议转人工客服获取帮助。", "relevance_score": 0.0}]

    return results[:3]  # 最多返回3条
