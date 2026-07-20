"""条件边路由逻辑 - 根据意图和情感状态决定Agent走向

路由策略:
1. 情感过激 → 强制转人工
2. 超过最大轮次 → 强制转人工
3. 多意图(sub_intents非空) → task_orchestrator编排执行
4. 单意图 → 按意图分类路由到对应子Agent
"""


def route_by_intent(state: dict) -> str:
    """根据意图分类结果路由到对应Agent节点

    路由优先级:
    1. 情感过激(angry + score > 0.8) → 强制转人工
    2. 超过最大轮次 → 强制转人工
    3. 多意图(sub_intents非空) → task_orchestrator
    4. 用户意图为human_escalation → 转人工
    5. 按意图分类路由到对应子Agent
    """
    # 优先级1: 情感过激强制转人工
    if state.get("needs_escalation", False):
        return "human_escalation"

    # 优先级2: 超过最大轮次转人工
    from app.core.config import settings
    if state.get("turn_count", 0) >= state.get("max_turns", settings.MAX_CONVERSATION_TURNS):
        return "human_escalation"

    # 优先级3: 多意图 → 走task_orchestrator编排
    sub_intents = state.get("sub_intents", [])
    if sub_intents and len(sub_intents) > 1:
        return "task_orchestrator"

    # 优先级4: 按意图路由
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


def route_after_orchestrator(state: dict) -> str:
    """task_orchestrator执行后路由: 还有没有子意图要处理?

    返回值:
    - 子Agent节点名: 继续执行下一个子意图
    - "response": 所有子意图已执行完，进入回复汇总
    """
    sub_intents = state.get("sub_intents", [])
    current_idx = state.get("current_sub_idx", 0)

    if current_idx >= len(sub_intents):
        # 全部执行完毕 → 进入response汇总
        return "response"

    # 还有子意图要处理 → 路由到对应子Agent
    current_task = sub_intents[current_idx]
    tool_hint = current_task.get("tool_hint", "general_chat")

    hint_to_node = {
        "order_query": "order_agent",
        "product_search": "product_agent",
        "refund_service": "refund_agent",
        "knowledge_faq": "knowledge_agent",
        "human_escalation": "escalation",
    }

    return hint_to_node.get(tool_hint, "response")


def route_after_sub_agent_in_orchestration(state: dict) -> str:
    """编排模式下子Agent执行后路由

    和_route_after_agent类似，但完成后回到task_orchestrator而非response
    """
    from app.agent.graph import SUB_AGENTS, _count_tool_messages, MAX_TOOL_CALLS

    messages = state.get("messages", [])
    if not messages:
        # 子Agent无工具调用 → 当前子意图处理完毕 → 回到orchestrator
        return "task_orchestrator"

    last_message = messages[-1]

    # 检查是否有待执行的工具调用(ReAct循环)
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        if _count_tool_messages(messages) >= MAX_TOOL_CALLS:
            return "task_orchestrator"

        react_steps = state.get("react_step_count", 0)
        max_steps = state.get("max_react_steps", 5)
        if react_steps >= max_steps:
            return "task_orchestrator"

        # 还有工具要调 → 继续ReAct循环
        active_agent = state.get("active_agent", "")
        if active_agent in SUB_AGENTS:
            return f"tool_executor_{active_agent}"

    # 子Agent无更多工具调用 → 当前子意图处理完毕 → 回到orchestrator
    return "task_orchestrator"
