"""LangGraph StateGraph构建与编译 - 整个Agent的核心骨架

图拓扑(单意图 — 原路径，80%场景):
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

图拓扑(多意图 — 编排路径，20%场景):
START → intent_router → task_orchestrator → [子Agent]
                                              ↙        ↘
                                        有tool_calls  无tool_calls
                                            ↓            ↓
                                       tool_executor_* task_orchestrator(下一个)
                                            ↓
                                       route_after_tool
                                        ↙          ↘
                                  有tool_calls    无
                                      ↓
                                  对应子Agent
                                  (ReAct循环)
当所有子意图执行完毕 → task_orchestrator → response

每个子Agent拥有独立的ToolNode，确保:
1. 工具隔离: 子Agent只能调用自己绑定的工具，无法越权
2. 错误容忍: handle_tool_errors=True，工具异常转为错误消息返回Agent
3. 资源限制: ReAct循环步数限制，防止无限循环

HITL(人工确认):
- 高风险ToolNode在执行前自动中断(interrupt_before)，等待用户确认后才继续执行
- 用户拒绝时，图收到Command(resume={"__approved__": False})，工具不执行，返回拒绝消息
- 低风险ToolNode(只读查询)不需要确认，直接执行
"""

import logging

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
from app.agent.edges import (
    route_by_intent,
    route_after_orchestrator,
)
from app.memory.summarizer import should_summarize, summarize_conversation

# === 工具导入 ===
from app.tools.order_query import query_order, track_logistics
from app.tools.product_search import search_products, check_inventory
from app.tools.refund import create_refund, create_service_ticket, query_refund_status
from app.tools.knowledge_rag import search_knowledge_base
from app.tools.human_escalation import transfer_to_human
from app.tools.phone_call import place_phone_call
from app.tools.sms import send_order_notification, send_refund_notification, send_custom_sms

logger = logging.getLogger(__name__)

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

# tool_hint → 子Agent节点名 映射
HINT_TO_AGENT = {
    "order_query": "order_agent",
    "product_search": "product_agent",
    "refund_service": "refund_agent",
    "knowledge_faq": "knowledge_agent",
    "human_escalation": "escalation",
}


# ============================================================
# task_orchestrator 节点 — 串行编排多意图
# ============================================================

async def task_orchestrator_node(state: CustomerServiceState) -> dict:
    """串行编排节点：按依赖顺序逐个执行子意图

    核心逻辑:
    1. 如果是子Agent执行后回到此节点(current_sub_idx对应的子意图已有AI回复)→先收集结果
    2. 收集完毕后推进current_sub_idx
    3. 检查下一个子意图，构造消息注入对应子Agent
    4. 如果全部子意图已执行完毕，不更新state，route_after_orchestrator路由到response
    """
    sub_intents = state.get("sub_intents", [])
    current_idx = state.get("current_sub_idx", 0)
    sub_results = list(state.get("sub_results", []))

    # === 步骤1: 收集上一个子意图的执行结果 ===
    if len(sub_results) < current_idx and current_idx > 0:
        messages = state.get("messages", [])
        last_ai_content = ""
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai":
                last_ai_content = msg.content
                break

        summary = last_ai_content[:300] if len(last_ai_content) > 300 else last_ai_content
        prev_task = sub_intents[current_idx - 1]
        completed_result = {
            "id": prev_task.get("id", current_idx),
            "intent": prev_task.get("intent", ""),
            "tool_hint": prev_task.get("tool_hint", ""),
            "result": last_ai_content,
            "summary": summary,
        }
        sub_results.append(completed_result)
        logger.info(
            "task_orchestrator: 收集子意图%d结果(共%d字符)",
            current_idx, len(last_ai_content),
        )

        # 推送 progress 事件: 子意图完成
        progress_event = {
            "type": "progress",
            "idx": current_idx,           # 已完成的子意图序号(1-based)
            "total": len(sub_intents),
            "task_id": completed_result["id"],
            "task_intent": completed_result["intent"],
            "task_hint": completed_result["tool_hint"],
            "result_summary": summary,
        }
    else:
        progress_event = None

    # === 步骤2: 检查是否全部执行完毕 ===
    if current_idx >= len(sub_intents):
        logger.info("task_orchestrator: 所有%d个子意图已执行完毕", len(sub_intents))
        all_summaries = "\n".join(
            f"- {r.get('intent', '?')}: {r.get('summary', '')}"
            for r in sub_results
        )
        return {
            "sub_results": sub_results,
            "conversation_summary": f"[多意图处理结果]\n{all_summaries}",
            "orchestrator_event": {
                "type": "complete",
                "total": len(sub_intents),
                "results": [{"id": r["id"], "intent": r["intent"], "summary": r["summary"]} for r in sub_results],
            },
        }

    # === 步骤3: 准备下一个子意图 ===
    current_task = sub_intents[current_idx]
    task_id = current_task.get("id", current_idx + 1)
    task_intent = current_task.get("intent", "")
    tool_hint = current_task.get("tool_hint", "general_chat")

    # 构造子意图的执行消息
    context_parts = []
    if sub_results:
        for r in sub_results:
            context_parts.append(f"子意图{r.get('id', '?')}结果: {r.get('summary', r.get('result', ''))}")
        context_text = "\n".join(context_parts)
    else:
        context_text = ""

    from langchain_core.messages import HumanMessage

    sub_message_content = task_intent
    if context_text:
        sub_message_content += f"\n\n[前序处理结果]\n{context_text}"

    target_agent = HINT_TO_AGENT.get(tool_hint, "")

    logger.info(
        "task_orchestrator: 执行子意图 %d/%d (id=%d, hint=%s, agent=%s): %s",
        current_idx + 1, len(sub_intents), task_id, tool_hint, target_agent, task_intent[:50],
    )

    # 首次进入编排时(current_idx==0且sub_results为空)推送 plan 事件
    if current_idx == 0 and not sub_results:
        orchestrator_event = {
            "type": "plan",
            "total": len(sub_intents),
            "tasks": [
                {"id": t.get("id", i + 1), "intent": t.get("intent", ""), "tool_hint": t.get("tool_hint", "")}
                for i, t in enumerate(sub_intents)
            ],
        }
    elif progress_event:
        orchestrator_event = progress_event
    else:
        orchestrator_event = None

    result = {
        "messages": [HumanMessage(content=sub_message_content)],
        "active_agent": target_agent,
        "intent": tool_hint,
        "current_sub_idx": current_idx + 1,
        "sub_results": sub_results,
    }
    if orchestrator_event:
        result["orchestrator_event"] = orchestrator_event
    return result


# ============================================================
# 图构建
# ============================================================

def build_graph() -> StateGraph:
    """构建客服Agent状态图"""
    builder = StateGraph(CustomerServiceState)

    # === 添加节点 ===
    builder.add_node("intent_router", intent_router_node)
    builder.add_node("task_orchestrator", task_orchestrator_node)
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

    # === 条件路由: intent_router → 单意图子Agent / 多意图task_orchestrator ===
    builder.add_conditional_edges(
        "intent_router",
        route_by_intent,
        {
            "task_orchestrator": "task_orchestrator",
            "order_agent": "order_agent",
            "product_agent": "product_agent",
            "refund_agent": "refund_agent",
            "knowledge_agent": "knowledge_agent",
            "escalation": "escalation",
            "response": "response",
        },
    )

    # === task_orchestrator → 子Agent(按tool_hint路由) 或 response ===
    builder.add_conditional_edges(
        "task_orchestrator",
        route_after_orchestrator,
        {
            "order_agent": "order_agent",
            "product_agent": "product_agent",
            "refund_agent": "refund_agent",
            "knowledge_agent": "knowledge_agent",
            "escalation": "escalation",
            "response": "response",
        },
    )

    # === 编排模式下子Agent → ReAct循环 或 collect_sub_result ===
    # 与单意图模式共享ToolNode，但完成后的路由不同
    for agent_name in SUB_AGENTS:
        builder.add_conditional_edges(
            agent_name,
            _route_after_agent_unified,
            {
                f"tool_executor_{agent_name}": f"tool_executor_{agent_name}",
                "task_orchestrator": "task_orchestrator",  # 编排模式：回到orchestrator
                "response": "response",                    # 单意图模式：到response
            },
        )

    # === ReAct循环: 各tool_executor → 路由回对应子Agent ===
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


def _route_after_agent_unified(state: CustomerServiceState) -> str:
    """子Agent执行后统一路由 — 自动判断单意图/多意图模式

    单意图模式: 有工具调用→tool_executor，否则→response
    多意图模式: 有工具调用→tool_executor，否则→task_orchestrator(收集结果后继续)
    """
    sub_intents = state.get("sub_intents", [])
    is_orchestration = bool(sub_intents) and len(sub_intents) > 1

    messages = state.get("messages", [])
    if not messages:
        return "task_orchestrator" if is_orchestration else "response"

    last_message = messages[-1]

    # 检查是否有待执行的工具调用
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        # 保护1: 超过最大工具调用次数则强制结束
        if _count_tool_messages(messages) >= MAX_TOOL_CALLS:
            return "task_orchestrator" if is_orchestration else "response"

        # 保护2: 超过最大ReAct步数则强制结束
        react_steps = state.get("react_step_count", 0)
        max_steps = state.get("max_react_steps", 5)
        if react_steps >= max_steps:
            return "task_orchestrator" if is_orchestration else "response"

        # 路由到当前Agent专属的tool_executor
        active_agent = state.get("active_agent", "")
        if active_agent in SUB_AGENTS:
            return f"tool_executor_{active_agent}"

    # 无工具调用 → 当前子意图处理完毕
    if is_orchestration:
        # 编排模式: 先收集结果，再继续下一个子意图
        # 但这里直接回到task_orchestrator，在orchestrator中收集
        return "task_orchestrator"
    else:
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
