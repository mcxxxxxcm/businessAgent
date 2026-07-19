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
    LLM_MAX_CONCURRENT: int = 5  # LLM最大并发数(Semaphore大小)
    LLM_QUEUE_TIMEOUT: float = 30.0  # 排队超时秒数(超时返回503而非无限等)
    LLM_STARTUP_CHECK: bool = True  # 启动时验证LLM API可达性(不可达则crash)

    # === PostgreSQL ===
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/cs_agent"

    # === Redis ===
    REDIS_URL: str = "redis://localhost:6379/0"

    # === 限流 ===
    RATE_LIMIT_PER_MINUTE: int = 30
    RATE_LIMIT_REDIS_FALLBACK: str = "allow"  # Redis不可用时限流策略: allow(放行)/deny(拒绝503)

    # === CORS ===
    CORS_ALLOWED_ORIGINS: str = ""  # 允许的来源域名(逗号分隔)，空则允许所有但不带credentials

    # === Agent ===
    MAX_CONVERSATION_TURNS: int = 20
    MAX_REACT_STEPS: int = 5  # 单次请求最大ReAct步数(子Agent内部循环)
    SESSION_TIMEOUT_MINUTES: int = 30
    GRAPH_EXECUTION_TIMEOUT: float = 60.0  # 单次请求总执行超时(秒)，防无限挂起
    SSE_PING_INTERVAL: float = 15.0  # SSE心跳间隔(秒)，检测Ghost连接

    # === 反馈 ===
    FEEDBACK_NEGATIVE_ESCALATION_THRESHOLD: int = 2  # 同session连续negative反馈触发转人工
    FEEDBACK_ENABLE_AUTO_ESCALATION: bool = True

    # === Checkpoint 清理 ===
    CHECKPOINT_CLEANUP_INTERVAL_MINUTES: int = 60  # 清理任务运行间隔(分钟)
    CHECKPOINT_KEEP_LATEST: bool = True  # 启用keep_latest策略: 每线程只保留最新checkpoint
    CHECKPOINT_THREAD_TTL_DAYS: int = 7  # 超过N天未活跃的线程整体删除
    CHECKPOINT_CLEANUP_BATCH_SIZE: int = 500  # 每批删除最大行数(防止长事务锁表)

    # === Store TTL ===
    STORE_DEFAULT_TTL_MINUTES: float = 43200  # Store默认TTL(30天 = 43200分钟)
    STORE_SWEEP_INTERVAL_MINUTES: int = 60  # Store TTL扫描间隔(分钟)

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

    # === JWT认证 ===
    JWT_SECRET_KEY: str = ""  # 必须设置，为空则启动crash
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # Token有效期7天
    AUTH_ENABLED: bool = True  # 认证开关(开发时可关闭)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
