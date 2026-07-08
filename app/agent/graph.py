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
from app.memory.summarizer import should_summarize, summarize_conversation

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

# 可路由回的子Agent列表
SUB_AGENTS = ["order_agent", "product_agent", "refund_agent", "knowledge_agent", "escalation"]


def build_graph() -> StateGraph:
    """构建客服Agent状态图 (支持ReAct自纠错循环 + CC记忆管理)

    图拓扑:
    START → intent_router → [子Agent]
                          ↙            ↘
                    有tool_calls      无tool_calls
                        ↓                ↓
                   tool_executor     response
                        ↓                ↓
                   route_after_tool   should_summarize?
                    ↙          ↘       ↙         ↘
              有tool_calls    无    summarize     END
              (且未超限)             ↓
                  ↓              END
              对应子Agent
              (ReAct循环)
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
    builder.add_node("summarize", summarize_conversation)

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

    # === 子Agent → 工具执行器 或 response ===
    for agent_name in SUB_AGENTS:
        builder.add_conditional_edges(
            agent_name,
            _route_after_agent,
            {
                "tool_executor": "tool_executor",
                "response": "response",
            },
        )

    # === ReAct循环: tool_executor → 路由回对应子Agent 或 response ===
    builder.add_conditional_edges(
        "tool_executor",
        _route_after_tool,
        {**{name: name for name in SUB_AGENTS}, "response": "response"},
    )

    # === response → 摘要检查 ===
    builder.add_conditional_edges(
        "response",
        should_summarize,
        {
            "summarize": "summarize",
            "end": END,
        },
    )

    # === 摘要完成后结束 ===
    builder.add_edge("summarize", END)

    return builder


MAX_TOOL_CALLS = 5  # 单次请求最大工具调用次数


def _count_tool_messages(messages: list) -> int:
    """统计消息中ToolMessage的数量(即已执行的工具调用次数)"""
    return sum(1 for m in messages if getattr(m, "type", None) == "tool")


def _route_after_agent(state: CustomerServiceState) -> str:
    """子Agent执行后路由: 有工具调用→tool_executor，否则→response"""
    messages = state.get("messages", [])
    if not messages:
        return "response"

    last_message = messages[-1]

    # 检查是否有待执行的工具调用
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        # 防无限循环: 超过最大工具调用次数则强制结束
        if _count_tool_messages(messages) >= MAX_TOOL_CALLS:
            return "response"
        return "tool_executor"

    return "response"


def _route_after_tool(state: CustomerServiceState) -> str:
    """工具执行后路由: ReAct循环核心

    工具执行后，把控制权交回给发起调用的子Agent，
    让它看到工具结果后自行决定:
    1. 结果OK → 生成最终回复(无tool_calls) → 下轮_route_after_agent→response
    2. 结果异常 → 再次调用工具(有tool_calls) → 下轮_route_after_agent→tool_executor
    3. 超过最大循环次数 → 强制到response
    """
    messages = state.get("messages", [])

    # 防无限循环: 超过最大工具调用次数则强制结束
    if _count_tool_messages(messages) >= MAX_TOOL_CALLS:
        return "response"

    # 路由回发起调用的子Agent
    active_agent = state.get("active_agent", "")
    if active_agent in SUB_AGENTS:
        return active_agent

    # 兜底: 不知道是谁调的工具，直接到response
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
