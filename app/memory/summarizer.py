"""对话摘要压缩 - Context Compression核心模块

参照LangGraph官方推荐的Summarization模式:
- 当对话历史超过token阈值时，自动生成摘要
- 摘要采用增量方式：已有摘要 + 新消息 → 扩展摘要
- 删除已摘要的旧消息，保留最近N条 + 摘要
- 摘要保存到PG Store用于跨会话检索
"""

import logging
from typing import Optional

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    RemoveMessage,
    BaseMessage,
)
from langgraph.store.base import BaseStore

from app.agent.state import CustomerServiceState

logger = logging.getLogger(__name__)

# === 阈值配置 ===
MAX_MESSAGES_BEFORE_SUMMARY = 20  # 消息数超过此值触发摘要
MIN_MESSAGES_TO_KEEP = 6  # 摘要后保留最近N条消息
MAX_SUMMARY_TOKENS = 512  # 摘要最大token数


def _count_messages_tokens(messages: list[BaseMessage]) -> int:
    """粗略估算消息列表的token数

    中文约1.5字/token，英文约4字符/token。
    粗略估计: 1个中文字≈1token, 1个英文词≈1.3token
    """
    total = 0
    for msg in messages:
        content = str(msg.content) if msg.content else ""
        # 粗略估算: 字符数 / 2
        total += len(content) // 2
        # 工具调用也占token
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            total += len(str(msg.tool_calls)) // 2
    return total


def should_summarize(state: CustomerServiceState) -> str:
    """判断是否需要摘要压缩

    Returns:
        "summarize" - 需要摘要
        "end" - 不需要，直接结束
    """
    messages = state.get("messages", [])

    # 消息数量检查
    if len(messages) > MAX_MESSAGES_BEFORE_SUMMARY:
        logger.info("触发摘要压缩: 消息数=%d > 阈值=%d", len(messages), MAX_MESSAGES_BEFORE_SUMMARY)
        return "summarize"

    # Token数量检查 (估算值)
    token_count = _count_messages_tokens(messages)
    if token_count > 4000:  # 约4000 token阈值
        logger.info("触发摘要压缩: 估算token=%d > 阈值=4000", token_count)
        return "summarize"

    return "end"


async def summarize_conversation(
    state: CustomerServiceState,
    *,
    store: BaseStore,
) -> dict:
    """对话摘要节点 - 压缩历史消息

    流程:
    1. 获取已有摘要（增量摘要）
    2. 选择需要摘要的旧消息（排除最近N条）
    3. 调用LLM生成/扩展摘要
    4. 删除已摘要的旧消息
    5. 保存摘要到长期记忆
    """
    from app.api.deps import get_llm, llm_semaphore

    messages = state.get("messages", [])
    existing_summary = state.get("conversation_summary", "")

    if len(messages) <= MIN_MESSAGES_TO_KEEP:
        return {}

    # 分离: 旧消息(要摘要) vs 近消息(要保留)
    messages_to_summarize = messages[:-MIN_MESSAGES_TO_KEEP]
    messages_to_keep = messages[-MIN_MESSAGES_TO_KEEP:]

    # 构建摘要请求
    if existing_summary:
        summary_prompt = (
            f"以下是之前的对话摘要:\n{existing_summary}\n\n"
            "请结合以上摘要和下面的新对话内容，生成一个更完整的摘要。\n"
            "摘要要求:\n"
            "1. 保留所有关键事实: 订单号、商品名、金额、日期等\n"
            "2. 保留用户问题、Agent回复的核心结论\n"
            "3. 保留情感变化和重要决策\n"
            "4. 删除重复和无关信息\n"
            "5. 摘要不超过300字\n\n"
            "新对话内容:\n"
        )
    else:
        summary_prompt = (
            "请为以下对话生成一个简洁的摘要。\n"
            "摘要要求:\n"
            "1. 保留所有关键事实: 订单号、商品名、金额、日期等\n"
            "2. 保留用户问题、Agent回复的核心结论\n"
            "3. 保留情感变化和重要决策\n"
            "4. 摘要不超过300字\n\n"
            "对话内容:\n"
        )

    # 将旧消息格式化为文本
    for msg in messages_to_summarize:
        role = "用户" if isinstance(msg, HumanMessage) else "客服"
        content = str(msg.content)[:200] if msg.content else "[工具调用]"
        summary_prompt += f"{role}: {content}\n"

    # 调用LLM生成摘要
    llm = get_llm()
    try:
        async with llm_semaphore:
            response = await llm.ainvoke(
                [
                    SystemMessage(content="你是一个对话摘要助手，擅长提取关键信息。"),
                    HumanMessage(content=summary_prompt),
                ]
            )
        new_summary = response.content
    except Exception as e:
        logger.warning("摘要生成失败: %s", e)
        return {}

    # 删除旧消息（通过RemoveMessage）
    remove_messages = [RemoveMessage(id=msg.id) for msg in messages_to_summarize]

    # 保存摘要到长期记忆
    user_id = state.get("user_id", "anonymous")
    session_id = state.get("session_id", "")
    await _save_session_summary(store, user_id, session_id, new_summary)

    logger.info(
        "摘要压缩完成: 删除%d条旧消息, 保留%d条近消息, 摘要长度=%d字",
        len(remove_messages),
        len(messages_to_keep),
        len(new_summary),
    )

    return {
        "messages": remove_messages,
        "conversation_summary": new_summary,
    }


async def _save_session_summary(
    store: BaseStore,
    user_id: str,
    session_id: str,
    summary: str,
) -> None:
    """保存会话摘要到长期记忆(跨会话可检索)"""
    try:
        ns = ("users", user_id, "session_summaries")
        await store.aput(ns, session_id, {"summary": summary, "session_id": session_id})
    except Exception as e:
        logger.warning("保存会话摘要失败: %s", e)
