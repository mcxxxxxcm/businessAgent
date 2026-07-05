"""Agent状态定义 - 所有节点共享的数据结构"""

from typing import TypedDict, Annotated, Literal, Optional

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class CustomerServiceState(TypedDict):
    """电商客服Agent状态

    遵循LangGraph "Keep state raw, format prompts on-demand" 原则，
    状态保持原始数据，在节点中按需格式化。
    """

    # 对话消息 - 使用add_messages reducer自动合并
    messages: Annotated[list[BaseMessage], add_messages]

    # 用户身份
    user_id: str
    session_id: str

    # 意图分类结果
    intent: Optional[
        Literal[
            "order_query",  # 订单查询/物流
            "product_search",  # 商品搜索/库存
            "refund_service",  # 退货退款/售后
            "knowledge_faq",  # 知识库FAQ
            "human_escalation",  # 转人工
            "general_chat",  # 通用闲聊
        ]
    ]

    # 情感分析(用于转人工判定)
    sentiment: Optional[Literal["positive", "neutral", "negative", "angry"]]
    sentiment_score: Optional[float]

    # 转人工标记
    needs_escalation: bool
    escalation_reason: Optional[str]

    # 循环防护
    turn_count: int  # 当前对话轮次
    max_turns: int  # 最大轮次(防无限循环)
