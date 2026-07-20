"""LangGraph StateGraph构建与编译 - 整个Agent的核心骨架

图拓扑:
START → intent_router → [子Agent]
                      ↙            ↘
                有tool_calls      无tool_calls
                    ↓                ↓
               tool_executor_*   response
                    ↓                ↓
               route_after_tool   should_summarize?
                ↙          ↘       ↙         ↘
          有tool_calls    无    summarize     END
          (且未超限)             ↓
              ↓              END
          对应子Agent
          (ReAct循环)

每个子Agent拥有独立的ToolNode，确保:
1. 工具隔离: 子Agent只能调用自己绑定的工具，无法越权
2. 错误容忍: handle_tool_errors=True，工具异常转为错误消息返回Agent
3. 资源限制: ReAct循环步数限制，防止无限循环

HITL(人工确认):
- 高风险ToolNode在执行前自动中断(interrupt_before)，等待用户确认后才继续执行
- 用户拒绝时，图收到Command(resume={"__approved__": False})，工具不执行，返回拒绝消息
- 低风险ToolNode(只读查询)不需要确认，直接执行
"""

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

# === 工具导入 ===
from app.tools.order_query import query_order, track_logistics
from app.tools.product_search import search_products, check_inventory
from app.tools.refund import create_refund, create_service_ticket, query_refund_status
from app.tools.knowledge_rag import search_knowledge_base
from app.tools.human_escalation import transfer_to_human
from app.tools.phone_call import place_phone_call
from app.tools.sms import send_order_notification, send_refund_notification, send_custom_sms

# === 按Agent分组的工具映射 ===
AGENT_TOOLS = {
    "order_agent": [query_order, track_logistics, send_order_notification],
    "product_agent": [search_products, check_inventory],
    "refund_agent": [create_refund, create_service_ticket, query_refund_status, send_refund_notification],
    "knowledge_agent": [search_knowledge_base],
    "escalation": [transfer_to_human, place_phone_call, send_custom_sms],
}

# 可路由回的子Agent列表
SUB_AGENTS = list(AGENT_TOOLS.keys())

# 所有工具(供引用)
ALL_TOOLS = [t for tools in AGENT_TOOLS.values() for t in tools]

MAX_TOOL_CALLS = 5  # 单次请求最大工具调用次数(双重保护)

# === 高风险ToolNode列表 — 需要人工确认后才执行 ===
# refund_agent: create_refund(创建退款), create_service_ticket(创建工单)
# escalation: place_phone_call(外呼), send_custom_sms(自定义短信)
HIGH_RISK_TOOL_NODES = [
    "tool_executor_refund_agent",
    "tool_executor_escalation",
]

# 高风险工具名称(用于前端展示确认信息)
HIGH_RISK_TOOL_NAMES = {
    "create_refund": "创建退款申请",
    "create_service_ticket": "创建售后工单",
    "place_phone_call": "拨打电话",
    "send_custom_sms": "发送短信",
}


def build_graph() -> StateGraph:
    """构建客服Agent状态图"""
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

    # 每个子Agent独立的ToolNode — 工具隔离 + 错误容忍
    for agent_name, tools in AGENT_TOOLS.items():
        builder.add_node(
            f"tool_executor_{agent_name}",
            ToolNode(tools, handle_tool_errors=True),
        )

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

    # === 子Agent → 对应的tool_executor 或 response ===
    for agent_name in SUB_AGENTS:
        builder.add_conditional_edges(
            agent_name,
            _route_after_agent,
            {
                f"tool_executor_{agent_name}": f"tool_executor_{agent_name}",
                "response": "response",
            },
        )

    # === ReAct循环: 各tool_executor → 路由回对应子Agent 或 response ===
    for agent_name in SUB_AGENTS:
        builder.add_conditional_edges(
            f"tool_executor_{agent_name}",
            _route_after_tool,
            {agent_name: agent_name, "response": "response"},
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


def _count_tool_messages(messages: list) -> int:
    """统计消息中ToolMessage的数量(即已执行的工具调用次数)"""
    return sum(1 for m in messages if getattr(m, "type", None) == "tool")


def _route_after_agent(state: CustomerServiceState) -> str:
    """子Agent执行后路由: 有工具调用→对应tool_executor，否则→response

    双重保护:
    1. ToolMessage计数 >= MAX_TOOL_CALLS → 强制response
    2. react_step_count >= max_react_steps → 强制response
    """
    messages = state.get("messages", [])
    if not messages:
        return "response"

    last_message = messages[-1]

    # 检查是否有待执行的工具调用
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        # 保护1: 超过最大工具调用次数则强制结束
        if _count_tool_messages(messages) >= MAX_TOOL_CALLS:
            return "response"

        # 保护2: 超过最大ReAct步数则强制结束
        react_steps = state.get("react_step_count", 0)
        max_steps = state.get("max_react_steps", 5)
        if react_steps >= max_steps:
            return "response"

        # 路由到当前Agent专属的tool_executor
        active_agent = state.get("active_agent", "")
        if active_agent in SUB_AGENTS:
            return f"tool_executor_{active_agent}"

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
    """编译图(带checkpointer、store和HITL中断)

    interrupt_before: 高风险ToolNode执行前暂停，等用户确认
    - 确认: 调用graph.stream(Command(resume={"__approved__": True}), config)
    - 拒绝: 调用graph.stream(Command(resume={"__approved__": False}), config)
    """
    builder = build_graph()
    graph = builder.compile(
        checkpointer=checkpointer,
        store=store,
        interrupt_before=HIGH_RISK_TOOL_NODES,
    )
    return graph
