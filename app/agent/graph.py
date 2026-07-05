"""LangGraph StateGraph构建与编译 - 整个Agent的核心骨架"""

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from app.agent.state import CustomerServiceState
from app.agent.nodes.intent_router import intent_router_node
from app.agent.nodes.order_agent import order_agent_node
from app.agent.nodes.product_agent import product_agent_node
from app.agent.nodes.refund_agent import refund_agent_node
from app.agent.nodes.knowledge_agent import knowledge_agent_node
from app.agent.nodes.escalation import escalation_node
from app.agent.nodes.response import response_node
from app.agent.edges import route_by_intent

# 所有工具列表(用于ToolNode)
from app.tools.order_query import query_order, track_logistics
from app.tools.product_search import search_products, check_inventory
from app.tools.refund import create_refund, create_service_ticket, query_refund_status
from app.tools.knowledge_rag import search_knowledge_base
from app.tools.human_escalation import transfer_to_human

ALL_TOOLS = [
    query_order,
    track_logistics,
    search_products,
    check_inventory,
    create_refund,
    create_service_ticket,
    query_refund_status,
    search_knowledge_base,
    transfer_to_human,
]


def build_graph() -> StateGraph:
    """构建客服Agent状态图

    图拓扑:
    START → intent_router → [order_agent | product_agent | refund_agent | knowledge_agent | escalation | response]
                           → tool_executor(如有工具调用) → 对应子Agent → response → END
    """
    builder = StateGraph(CustomerServiceState)

    # === 添加节点 ===
    builder.add_node("intent_router", intent_router_node)
    builder.add_node("order_agent", order_agent_node)
    builder.add_node("product_agent", product_agent_node)
    builder.add_node("refund_agent", refund_agent_node)
    builder.add_node("knowledge_agent", knowledge_agent_node)
    builder.add_node("escalation", escalation_node)
    builder.add_node("response", response_node)

    # ToolNode: 统一处理所有工具调用
    builder.add_node("tool_executor", ToolNode(ALL_TOOLS))

    # === 入口边 ===
    builder.add_edge(START, "intent_router")

    # === 条件路由: 意图分类后分发到对应Agent ===
    builder.add_conditional_edges(
        "intent_router",
        route_by_intent,
        {
            "order_agent": "order_agent",
            "product_agent": "product_agent",
            "refund_agent": "refund_agent",
            "knowledge_agent": "knowledge_agent",
            "escalation": "escalation",
            "response": "response",
        },
    )

    # === 子Agent → 工具执行器(如有工具调用) ===
    # 每个子Agent可能需要调用工具，需要路由到tool_executor
    for agent_name in ["order_agent", "product_agent", "refund_agent", "knowledge_agent", "escalation"]:
        builder.add_conditional_edges(
            agent_name,
            _route_after_agent,
            {
                "tool_executor": "tool_executor",
                "response": "response",
            },
        )

    # === 工具执行器 → 回到对应的子Agent(继续处理) ===
    # 简化处理：工具执行后直接到response节点生成最终回复
    builder.add_edge("tool_executor", "response")

    # === response → END ===
    builder.add_edge("response", END)

    return builder


def _route_after_agent(state: CustomerServiceState) -> str:
    """子Agent执行后路由: 有工具调用→tool_executor，否则→response"""
    messages = state.get("messages", [])
    if not messages:
        return "response"

    last_message = messages[-1]

    # 检查是否有待执行的工具调用
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tool_executor"

    return "response"


async def compile_graph(checkpointer, store):
    """编译图(带checkpointer和store)

    Args:
        checkpointer: PostgreSQL AsyncPostgresSaver实例
        store: PostgreSQL AsyncPostgresStore实例

    Returns:
        编译好的Runnable图
    """
    builder = build_graph()
    graph = builder.compile(
        checkpointer=checkpointer,
        store=store,
    )
    return graph
