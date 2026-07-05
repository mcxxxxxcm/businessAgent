"""自定义异常体系"""


class AgentError(Exception):
    """Agent基础异常"""

    def __init__(self, message: str, code: str = "AGENT_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class LLMError(AgentError):
    """LLM调用异常"""

    def __init__(self, message: str):
        super().__init__(message, code="LLM_ERROR")


class ToolError(AgentError):
    """工具调用异常"""

    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"工具 {tool_name} 调用失败: {message}", code="TOOL_ERROR")


class RateLimitError(AgentError):
    """限流异常"""

    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"请求过于频繁，请 {retry_after} 秒后重试", code="RATE_LIMIT")


class SessionExpiredError(AgentError):
    """会话过期异常"""

    def __init__(self, session_id: str):
        super().__init__(f"会话 {session_id} 已过期", code="SESSION_EXPIRED")
