"""Pydantic请求/响应模型"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天请求"""

    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="用户消息",
    )
    user_id: str = Field(
        ...,
        min_length=1,
        description="用户ID",
    )
    session_id: str | None = Field(
        None,
        description="会话ID，不传则创建新会话",
    )


class ResponseMeta(BaseModel):
    """回复元数据(AgentResponseMeta的结构化输出)"""

    response_type: str = "direct_answer"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    suggested_actions: list[str] = Field(default_factory=list)
    needs_followup: bool = False


class ChatResponse(BaseModel):
    """聊天响应(非流式)"""

    session_id: str
    reply: str
    intent: str | None = None
    sentiment: str | None = None
    needs_escalation: bool = False
    response_meta: ResponseMeta | None = None


class SSEEvent(BaseModel):
    """SSE事件"""

    event: str
    data: dict


class SessionInfo(BaseModel):
    """会话信息"""

    session_id: str
    user_id: str
    message_count: int
    last_intent: str | None = None
    last_sentiment: str | None = None


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str
    redis: str
    postgres: str
    version: str


class ErrorResponse(BaseModel):
    """错误响应"""

    error: dict
