"""对话摘要压缩 - Context Compression核心模块

参照LangGraph官方推荐的Summarization模式:
- 当对话历史超过token阈值时，自动生成摘要
- 摘要采用增量方式：已有摘要 + 新消息 → 扩展摘要
- 删除已摘要的旧消息，保留最近N条 + 摘要
- 摘要保存到PG Store用于跨会话检索

结构化输出: 使用ConversationSummary Pydantic模型 + structured_llm_output四层降级链
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
from app.agent.schemas import ConversationSummary, structured_llm_output

logger = logging.getLogger(__name__)

# === 阈值配置 ===
MAX_MESSAGES_BEFORE_SUMMARY = 20  # 消息数超过此值触发摘要
MIN_MESSAGES_TO_KEEP = 6  # 摘要后保留最近N条消息
MAX_SUMMARY_TOKENS = 512  # 摘要最大token数


def _count_messages_tokens(messages: list[BaseMessage]) -> int:
    """估算消息列表的token数

    经验公式:
    - 中文: 1字 ≈ 1.5 token (汉字在GPT/BPE分词中通常1-2 token)
    - 英文: 1词 ≈ 1.3 token ≈ 4字符/token
    - 工具调用: JSON结构约3字符/token
    - 每条消息额外开销约4 token (角色标记等)

    对比旧公式 len(content)//2:
    - 旧: "你好世界" → 4//2 = 2 token (严重低估，实际约6)
    - 新: "你好世界" → 4*1.5 + 4 = 10 token (保守但安全)
    """
    import re
    total = 0
    for msg in messages:
        content = str(msg.content) if msg.content else ""
        if not content:
            continue
        # 统计中文字符数
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", content))
        # 非中文字符数(英文、标点、数字等)
        non_chinese = len(content) - chinese_chars
        # 中文1字≈1.5token，英文4字符≈1token，+4消息开销
        total += int(chinese_chars * 1.5 + non_chinese / 4) + 4
        # 工具调用额外计算
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            total += int(len(str(msg.tool_calls)) / 3)
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
    3. 调用LLM生成/扩展摘要（结构化输出ConversationSummary）
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
        summary_instruction = (
            f"以下是之前的对话摘要:\n{existing_summary}\n\n"
            "请结合以上摘要和下面的新对话内容，生成一个更完整的结构化摘要。\n"
        )
    else:
        summary_instruction = "请为以下对话生成一个结构化摘要。\n"

    summary_instruction += "摘要要求:\n1. 保留所有关键事实(订单号、商品名、金额、日期等)\n2. 保留用户问题、Agent回复的核心结论\n3. 保留情感变化和重要决策\n4. 摘要正文不超过300字\n"

    # 将旧消息格式化为文本
    msg_text = ""
    for msg in messages_to_summarize:
        role = "用户" if isinstance(msg, HumanMessage) else "客服"
        content = str(msg.content) if msg.content else "[工具调用]"
        # 分类型截断: ToolMessage完整保留，AI消息500字，HumanMessage 200字
        if getattr(msg, "type", None) == "tool":
            pass  # 工具返回值完整保留(含关键数据)
        elif getattr(msg, "type", None) == "ai":
            content = content[:500]  # AI回复可能较长，保留更多
        else:
            content = content[:200]  # 用户消息通常较短
        msg_text += f"{role}: {content}\n"

    # 调用LLM生成结构化摘要(四层降级链)
    llm = get_llm()
    from app.api.deps import llm_semaphore

    # 构建messages用于结构化输出
    summary_messages = [
        SystemMessage(content="你是一个对话摘要助手，擅长提取关键信息并结构化输出。"),
        HumanMessage(content=summary_instruction + "\n对话内容:\n" + msg_text),
    ]

    summary_result = await structured_llm_output(
        model_class=ConversationSummary,
        llm=llm,
        messages=summary_messages,
        semaphore=llm_semaphore,
    )

    # 解析失败，降级为纯文本摘要
    if summary_result is None:
        logger.warning("结构化摘要解析失败，降级为纯文本")
        try:
            response = await llm.ainvoke([
                SystemMessage(content="你是一个对话摘要助手，擅长提取关键信息。请生成不超过300字的摘要。"),
                HumanMessage(content=summary_instruction + "\n对话内容:\n" + msg_text),
            ])
            new_summary = response.content
        except Exception as e:
            logger.warning("纯文本摘要也失败: %s", e)
            return {}
    else:
        # 结构化摘要成功，转为存储格式
        new_summary = summary_result.summary_text

        # 将结构化数据也保存（key_facts等）
        summary_meta = summary_result.model_dump()
        summary_meta["summary"] = new_summary
        user_id = state.get("user_id", "anonymous")
        session_id = state.get("session_id", "")
        await _save_session_summary(store, user_id, session_id, new_summary, extra_data=summary_meta)

        # 删除旧消息（通过RemoveMessage）
        remove_messages = [RemoveMessage(id=msg.id) for msg in messages_to_summarize]

        logger.info(
            "摘要压缩完成: 删除%d条旧消息, 保留%d条近消息, 话题=%s, 已解决=%s",
            len(remove_messages),
            len(messages_to_keep),
            summary_result.key_topics,
            summary_result.resolved,
        )

        return {
            "messages": remove_messages,
            "conversation_summary": new_summary,
        }

    # 纯文本摘要的路径
    remove_messages = [RemoveMessage(id=msg.id) for msg in messages_to_summarize]
    user_id = state.get("user_id", "anonymous")
    session_id = state.get("session_id", "")
    await _save_session_summary(store, user_id, session_id, new_summary)

    logger.info(
        "摘要压缩完成(纯文本): 删除%d条旧消息, 保留%d条近消息, 摘要长度=%d字",
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
    extra_data: Optional[dict] = None,
) -> None:
    """保存会话摘要到长期记忆(跨会话可检索)"""
    try:
        ns = ("users", user_id, "session_summaries")
        data = {"summary": summary, "session_id": session_id}
        if extra_data:
            data.update(extra_data)
        await store.aput(ns, session_id, data)
    except Exception as e:
        logger.warning("保存会话摘要失败: %s", e)
