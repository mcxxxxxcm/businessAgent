"""应用配置管理 - 基于 Pydantic Settings"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """全局配置，从环境变量或.env文件加载"""

    # === LLM ===
    ZHIPU_API_KEY: str = ""
    ZHIPU_MODEL: str = "glm-4"
    ZHIPU_API_BASE: str = "https://open.bigmodel.cn/api/paas/v4/"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 2048

    # === PostgreSQL ===
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/cs_agent"

    # === Redis ===
    REDIS_URL: str = "redis://localhost:6379/0"

    # === 限流 ===
    RATE_LIMIT_PER_MINUTE: int = 30

    # === Agent ===
    MAX_CONVERSATION_TURNS: int = 20
    SESSION_TIMEOUT_MINUTES: int = 30

    # === LangSmith (可选) ===
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str | None = None
    LANGCHAIN_PROJECT: str = "cs-agent"

    # === 应用 ===
    APP_NAME: str = "智能客服Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
