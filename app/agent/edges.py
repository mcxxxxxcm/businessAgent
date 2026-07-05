"""条件边路由逻辑 - 根据意图和情感状态决定Agent走向"""

from app.agent.state import CustomerServiceState


def route_by_intent(state: CustomerServiceState) -> str:
    """根据意图分类结果路由到对应Agent节点

    路由优先级:
    1. 情感过激(angry + score > 0.8) → 强制转人工
    2. 超过最大轮次 → 强制转人工
    3. 用户意图为human_escalation → 转人工
    4. 按意图分类路由到对应子Agent
    """
    # 优先级1: 情感过激强制转人工
    if state.get("needs_escalation", False):
        return "human_escalation"

    # 优先级2: 超过最大轮次转人工
    if state.get("turn_count", 0) >= state.get("max_turns", 20):
        return "human_escalation"

    # 优先级3: 按意图路由
    intent = state.get("intent", "general_chat")

    intent_to_node = {
        "order_query": "order_agent",
        "product_search": "product_agent",
        "refund_service": "refund_agent",
        "knowledge_faq": "knowledge_agent",
        "human_escalation": "escalation",
        "general_chat": "response",
    }

    return intent_to_node.get(intent, "response")
