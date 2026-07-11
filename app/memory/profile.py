"""用户画像管理 - 长期记忆核心模块

管理跨会话的用户偏好、关键事实、交互历史。
数据存储在PG Store中，按命名空间组织:
- (users, {user_id}, profile)       → 用户基本画像
- (users, {user_id}, facts)         → 关键事实(订单号、偏好等)
- (users, {user_id}, session_summaries) → 历史会话摘要
"""

import logging
from typing import Optional

from pydantic import BaseModel, Field
from langgraph.store.base import BaseStore

logger = logging.getLogger(__name__)


# === 画像结构定义 (Pydantic BaseModel) ===

class UserProfile(BaseModel):
    """用户画像数据结构 - Pydantic校验

    所有字段有默认值，支持从空数据创建。
    字段约束确保数据一致性。
    """
    # 基础信息
    total_conversations: int = Field(default=0, ge=0, description="历史交互次数")
    first_seen: str = Field(default="", description="首次交互时间")
    last_seen: str = Field(default="", description="最近交互时间")

    # 交互偏好
    frequent_intents: dict[str, int] = Field(
        default_factory=dict,
        description="意图频率分布，如{'order_query': 5, 'product_search': 3}",
    )
    avg_sentiment: str = Field(default="neutral", description="平均情感倾向")

    # 关键事实
    recent_orders: list[str] = Field(
        default_factory=list,
        description="最近查询的订单号，最多10个",
    )
    recent_products: list[str] = Field(
        default_factory=list,
        description="最近搜索的商品，最多10个",
    )
    issues: list[dict] = Field(
        default_factory=list,
        description="历史问题记录，最多20条",
    )

    # 满意度
    escalation_count: int = Field(default=0, ge=0, description="历史转人工次数")
    complaint_count: int = Field(default=0, ge=0, description="历史投诉次数")
    satisfaction_scores: list[str] = Field(
        default_factory=list,
        description="最近满意度评价，最多20条",
    )

    def to_prompt_text(self) -> str:
        """将画像转为可注入prompt的文本"""
        parts = []

        if self.total_conversations > 0:
            parts.append(f"历史交互次数: {self.total_conversations}")

        if self.frequent_intents:
            top_intents = sorted(
                self.frequent_intents.items(), key=lambda x: x[1], reverse=True
            )[:3]
            intent_str = ", ".join(f"{k}({v}次)" for k, v in top_intents)
            parts.append(f"常见需求: {intent_str}")

        if self.recent_orders:
            parts.append(f"最近查询的订单: {', '.join(self.recent_orders[-3:])}")

        if self.recent_products:
            parts.append(f"最近搜索的商品: {', '.join(self.recent_products[-3:])}")

        if self.escalation_count > 0:
            parts.append(f"历史转人工次数: {self.escalation_count}")

        if self.complaint_count > 0:
            parts.append(f"历史投诉次数: {self.complaint_count}")

        if self.issues:
            latest = self.issues[-1]
            parts.append(f"最近问题: {latest.get('summary', '未知')}")

        return "\n".join(parts) if parts else ""


# === 画像操作 ===

async def load_user_profile(store: BaseStore, user_id: str) -> UserProfile:
    """从PG Store加载用户画像"""
    ns = ("users", user_id, "profile")
    try:
        existing = await store.aget(ns, "main")
        if existing:
            return UserProfile(**existing.value)
    except Exception as e:
        logger.warning("加载用户画像失败: %s", e)

    return UserProfile()


async def save_user_profile(store: BaseStore, user_id: str, profile: UserProfile) -> None:
    """保存用户画像到PG Store"""
    ns = ("users", user_id, "profile")
    try:
        # Pydantic模型序列化时限制列表长度
        data = profile.model_dump()
        data["recent_orders"] = data["recent_orders"][-10:]
        data["recent_products"] = data["recent_products"][-10:]
        data["issues"] = data["issues"][-20:]
        await store.aput(ns, "main", data)
    except Exception as e:
        logger.warning("保存用户画像失败: %s", e)


async def update_profile_from_state(
    store: BaseStore,
    user_id: str,
    intent: str | None,
    sentiment: str | None,
    messages: list,
) -> None:
    """根据当前对话状态更新用户画像

    在response节点结束时调用，增量更新画像数据。
    支持两种事实提取方式:
    1. 规则匹配(快速，无LLM调用) - 默认
    2. LLM提取(准确，有额外调用) - 按需启用
    """
    import datetime

    profile = await load_user_profile(store, user_id)

    # 更新交互次数
    profile.total_conversations += 1

    # 更新最后交互时间
    profile.last_seen = datetime.datetime.now().isoformat()
    if not profile.first_seen:
        profile.first_seen = profile.last_seen

    # 更新意图偏好
    if intent:
        intents = profile.frequent_intents
        intents[intent] = intents.get(intent, 0) + 1
        profile.frequent_intents = intents

    # 更新情感
    if sentiment == "angry":
        profile.complaint_count += 1
    elif sentiment == "negative":
        profile.complaint_count += 1

    # 从消息中提取关键事实(规则匹配，快速)
    _extract_facts_from_messages(profile, messages)

    await save_user_profile(store, user_id, profile)


async def update_profile_with_llm_extraction(
    store: BaseStore,
    user_id: str,
    messages: list,
) -> None:
    """使用LLM提取关键事实更新用户画像(可选，更精确)

    使用structured_llm_output四层降级链进行结构化事实提取。
    当规则匹配不够准确时调用，会产生额外的LLM调用。
    """
    from app.api.deps import get_llm, llm_semaphore
    from app.agent.schemas import FactExtraction, structured_llm_output

    # 只取最近10条消息
    recent = messages[-10:]
    if not recent:
        return

    # 构建消息文本
    msg_text = ""
    for msg in recent:
        role = "用户" if hasattr(msg, "type") and msg.type == "human" else "客服"
        content = str(msg.content)[:300] if msg.content else ""
        if content:
            msg_text += f"{role}: {content}\n"

    if not msg_text.strip():
        return

    from langchain_core.messages import SystemMessage, HumanMessage
    extraction_messages = [
        SystemMessage(content="你是一个信息提取助手。请从对话中提取关键结构化事实。"),
        HumanMessage(content=f"请从以下对话中提取关键事实:\n\n{msg_text}"),
    ]

    llm = get_llm()
    result = await structured_llm_output(
        model_class=FactExtraction,
        llm=llm,
        messages=extraction_messages,
        semaphore=llm_semaphore,
    )

    if result is None:
        logger.warning("LLM事实提取失败(4层降级均失败)，跳过")
        return

    # 更新画像
    profile = await load_user_profile(store, user_id)

    for order_id in result.order_ids:
        if order_id not in profile.recent_orders:
            profile.recent_orders.append(order_id)

    for product in result.product_names:
        if product not in profile.recent_products:
            profile.recent_products.append(product)

    if result.complaint_reason:
        profile.issues.append({"summary": result.complaint_reason})

    await save_user_profile(store, user_id, profile)
    logger.info("LLM事实提取完成: orders=%s, products=%s", result.order_ids, result.product_names)


async def load_recent_summaries(
    store: BaseStore,
    user_id: str,
    limit: int = 3,
) -> str:
    """加载最近N次会话的摘要(用于新会话的上下文注入)"""
    ns = ("users", user_id, "session_summaries")
    try:
        items = await store.asearch(ns, query="recent", limit=limit)
        if items:
            summaries = [item.value.get("summary", "") for item in items if item.value.get("summary")]
            if summaries:
                return "历史会话摘要:\n" + "\n---\n".join(summaries)
    except Exception as e:
        logger.warning("加载历史摘要失败: %s", e)

    return ""


def _extract_facts_from_messages(profile: UserProfile, messages: list) -> None:
    """从对话消息中提取关键事实(订单号、商品名等)

    使用简单的规则匹配，避免额外的LLM调用。
    """
    import re

    for msg in messages:
        content = str(msg.content) if hasattr(msg, "content") and msg.content else ""
        if not content:
            continue

        # 提取订单号 (ORD开头的编号)
        order_matches = re.findall(r"ORD\d+", content)
        for order_id in order_matches:
            if order_id not in profile.recent_orders:
                profile.recent_orders.append(order_id)

        # 提取可能的商品关键词 (简单启发式: "XX耳机"/"XX手表"/"XX键盘"等)
        product_matches = re.findall(
            r"(?:蓝牙|无线|智能|机械|游戏)\S{1,10}(?:耳机|手表|键盘|鼠标|手环|音箱|平板|手机)",
            content,
        )
        for product in product_matches:
            if product not in profile.recent_products:
                profile.recent_products.append(product)


async def update_satisfaction_score(user_id: str, rating: str) -> None:
    """更新用户画像中的满意度评价(由反馈API调用)

    Args:
        user_id: 用户ID
        rating: "positive" 或 "negative"
    """
    from app.api.deps import get_store

    store = await get_store()
    profile = await load_user_profile(store, user_id)

    profile.satisfaction_scores.append(rating)
    # 只保留最近20条
    if len(profile.satisfaction_scores) > 20:
        profile.satisfaction_scores = profile.satisfaction_scores[-20:]

    await save_user_profile(store, user_id, profile)
