"""售后退款Agent节点"""

import logging

from langchain_core.messages import AIMessage

from app.agent.state import CustomerServiceState
from app.agent.prompts import REFUND_AGENT_PROMPT
from app.tools.refund import create_refund, create_service_ticket, query_refund_status
from app.tools.sms import send_refund_notification

logger = logging.getLogger(__name__)


async def refund_agent_node(state: CustomerServiceState) -> dict:
    """售后退款Agent - 绑定退款工具和退款通知短信工具

    HITL: 首次调用时使用tool_choice="required"强制调用工具,
    确保路由到tool_executor_refund_agent触发interrupt_before人工确认。
    ReAct循环中(已有工具结果)则使用"auto"允许LLM生成文字回复。
    """
    from app.api.deps import get_llm, acquire_llm_semaphore, release_llm_semaphore, LLMQueueTimeoutError
    from app.memory.manager import build_agent_prompt_input

    llm = get_llm()

    # HITL关键: 首次调用强制工具调用,确保触发interrupt_before
    # 判断依据: 最后一条消息是否为ToolMessage(即是否在ReAct循环中)
    messages = state.get("messages", [])
    last_is_tool_result = bool(messages) and getattr(messages[-1], "type", None) == "tool"
    tool_choice = "auto" if last_is_tool_result else "required"
    logger.info("refund_agent: tool_choice=%s (last_is_tool_result=%s)", tool_choice, last_is_tool_result)

    chain = (
        REFUND_AGENT_PROMPT
        | llm.bind_tools(
            [create_refund, create_service_ticket, query_refund_status, send_refund_notification],
            tool_choice=tool_choice,
        )
    )

    prompt_input = build_agent_prompt_input(state)

    try:
        await acquire_llm_semaphore()
        try:
            response = await chain.ainvoke(prompt_input)
        finally:
            release_llm_semaphore()
    except LLMQueueTimeoutError:
        logger.warning("refund_agent LLM排队超时，返回503")
        response = AIMessage(content="当前服务繁忙，请稍后重试或联系人工客服。")
    except Exception as e:
        logger.error("refund_agent LLM调用失败: %s", e)
        response = AIMessage(content="抱歉，处理退款申请时遇到了问题，请稍后重试或联系人工客服。")

    # === HITL关键: LLM未调用工具时，手动构造tool_call ===
    # GLM-4-flash不支持tool_choice="required"，可能返回纯文字AIMessage
    # 需要手动构造create_refund tool_call，才能路由到tool_executor触发interrupt_before
    has_tool_calls = isinstance(response, AIMessage) and bool(response.tool_calls)
    logger.info(
        "refund_agent: response_type=%s, has_tool_calls=%s, tool_calls=%s, content_len=%d",
        type(response).__name__, has_tool_calls,
        [tc.get("name", "") for tc in response.tool_calls] if has_tool_calls else [],
        len(getattr(response, "content", "") or ""),
    )
    if isinstance(response, AIMessage) and not response.tool_calls and not last_is_tool_result:
        logger.warning("refund_agent: LLM未调用工具(tool_choice=required被忽略)，手动构造create_refund tool_call")
        response = _force_refund_tool_call(messages, response)
        logger.info("refund_agent: 手动构造后 tool_calls=%s", [tc.get("name", "") for tc in response.tool_calls])

    return {
        "messages": [response],
        "active_agent": "refund_agent",
        "react_step_count": state.get("react_step_count", 0) + 1,
    }


def _force_refund_tool_call(messages: list, original_response: AIMessage) -> AIMessage:
    """当LLM返回纯文字而非工具调用时，手动构造create_refund tool_call

    从对话消息中提取order_id和reason，构造AIMessage包含tool_calls，
    使图路由到tool_executor_refund_agent，触发interrupt_before HITL确认。
    """
    import re
    import uuid

    # 从消息中提取order_id和reason
    order_id = ""
    reason = "用户申请退款"

    # 合并所有消息文本用于提取
    all_text = " ".join(
        getattr(msg, "content", "") for msg in messages if hasattr(msg, "content")
    )

    # 提取订单号: 常见模式 ORDxxx / 纯数字6位+ / 中文"订单xxx"
    order_patterns = [
        r"订单[号编号]?\s*[:：]?\s*(ORD[\w]+|\d{6,})",  # 订单号: ORDxxx 或 数字
        r"(ORD[\w]+)",                                    # ORD前缀
        r"订单\s*(\d{6,})",                               # 订单+数字
        r"(\d{6,})",                                      # 纯数字6位以上
    ]
    for pattern in order_patterns:
        match = re.search(pattern, all_text)
        if match:
            order_id = match.group(1)
            break

    # 提取退款原因
    reason_patterns = [
        r"退款原因\s*[:：]?\s*(.+?)(?:[。，,；;]|$)",
        r"因为\s*(.+?)(?:[。，,；;]|$)",
        r"由于\s*(.+?)(?:[。，,；;]|$)",
    ]
    for pattern in reason_patterns:
        match = re.search(pattern, all_text)
        if match:
            reason = match.group(1).strip()[:50]
            break

    # 构造create_refund tool_call
    tool_call_id = f"call_{uuid.uuid4().hex[:24]}"
    tool_call_args = {
        "order_id": order_id or "UNKNOWN",
        "reason": reason,
        "refund_type": "return_refund",
    }

    logger.info(
        "手动构造tool_call: create_refund(order_id=%s, reason=%s)",
        tool_call_args["order_id"], tool_call_args["reason"],
    )

    return AIMessage(
        content="",
        tool_calls=[{
            "name": "create_refund",
            "args": tool_call_args,
            "id": tool_call_id,
        }],
    )
