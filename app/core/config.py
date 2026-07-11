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
    MAX_REACT_STEPS: int = 5  # 单次请求最大ReAct步数(子Agent内部循环)
    SESSION_TIMEOUT_MINUTES: int = 30

    # === 反馈 ===
    FEEDBACK_NEGATIVE_ESCALATION_THRESHOLD: int = 2  # 同session连续negative反馈触发转人工
    FEEDBACK_ENABLE_AUTO_ESCALATION: bool = True

    # === LangSmith (可选) ===
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str | None = None
    LANGCHAIN_PROJECT: str = "cs-agent"

    # === 短信服务 ===
    SMS_PROVIDER: str = "dummy"  # aliyun / tencent / dummy(模拟)
    SMS_SIGN_NAME: str = "智能优选"  # 短信签名，需在平台审核
    # 阿里云短信
    ALIYUN_ACCESS_KEY_ID: str = ""
    ALIYUN_ACCESS_KEY_SECRET: str = ""
    # 腾讯云短信
    TENCENT_SECRET_ID: str = ""
    TENCENT_SECRET_KEY: str = ""
    TENCENT_SMS_APP_ID: str = ""
    # 通用
    SMS_DRY_RUN: bool = True  # True=只打印不实际发送(开发模式)

    # === 应用 ===
    APP_NAME: str = "智能客服Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
