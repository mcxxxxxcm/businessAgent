"""Agent状态定义 - 所有节点共享的数据结构"""

from typing import TypedDict, Annotated, Literal, Optional, Any

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

    # 当前活跃的子Agent(用于ReAct循环: tool_executor路由回对应的子Agent)
    active_agent: Optional[str]  # "order_agent" | "product_agent" | "refund_agent" | "knowledge_agent" | "escalation"

    # === 记忆相关字段 ===

    # 当前会话的对话摘要(增量摘要，Context Compression核心)
    conversation_summary: str

    # 用户画像(从长期记忆加载，注入prompt)
    user_profile: Optional[dict]

    # 历史会话摘要(跨会话上下文)
    history_summary: str

    # 对话轮次计数
    turn_count: int

    # 最大对话轮次(超过自动转人工)
    max_turns: int

    # 最终回复元数据(AgentResponseMeta结构化输出)
    response_meta: Optional[dict]

    # ReAct循环步数计数(子Agent内部循环，防止资源爆满)
    react_step_count: int

    # 单次请求最大ReAct步数(超过强制进入response)
    max_react_steps: int
