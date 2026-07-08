"""用户画像管理 - 长期记忆核心模块

管理跨会话的用户偏好、关键事实、交互历史。
数据存储在PG Store中，按命名空间组织:
- (users, {user_id}, profile)       → 用户基本画像
- (users, {user_id}, facts)         → 关键事实(订单号、偏好等)
- (users, {user_id}, session_summaries) → 历史会话摘要
"""

import json
import logging
from typing import Optional

from langgraph.store.base import BaseStore

logger = logging.getLogger(__name__)


# === 画像结构定义 ===

class UserProfile:
    """用户画像数据结构"""

    def __init__(self, data: dict | None = None):
        data = data or {}
        # 基础信息
        self.total_conversations: int = data.get("total_conversations", 0)
        self.first_seen: str = data.get("first_seen", "")
        self.last_seen: str = data.get("last_seen", "")

        # 交互偏好
        self.frequent_intents: dict[str, int] = data.get("frequent_intents", {})
        self.avg_sentiment: str = data.get("avg_sentiment", "neutral")

        # 关键事实
        self.recent_orders: list[str] = data.get("recent_orders", [])  # 最近查询的订单号
        self.recent_products: list[str] = data.get("recent_products", [])  # 最近搜索的商品
        self.issues: list[dict] = data.get("issues", [])  # 历史问题记录

        # 满意度
        self.escalation_count: int = data.get("escalation_count", 0)
        self.complaint_count: int = data.get("complaint_count", 0)

    def to_dict(self) -> dict:
        return {
            "total_conversations": self.total_conversations,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "frequent_intents": self.frequent_intents,
            "avg_sentiment": self.avg_sentiment,
            "recent_orders": self.recent_orders[-10:],  # 最多保留10个
            "recent_products": self.recent_products[-10:],
            "issues": self.issues[-20:],  # 最多保留20条
            "escalation_count": self.escalation_count,
            "complaint_count": self.complaint_count,
        }

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
            return UserProfile(existing.value)
    except Exception as e:
        logger.warning("加载用户画像失败: %s", e)

    return UserProfile()


async def save_user_profile(store: BaseStore, user_id: str, profile: UserProfile) -> None:
    """保存用户画像到PG Store"""
    ns = ("users", user_id, "profile")
    try:
        await store.aput(ns, "main", profile.to_dict())
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

    # 从消息中提取关键事实
    _extract_facts_from_messages(profile, messages)

    await save_user_profile(store, user_id, profile)


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
