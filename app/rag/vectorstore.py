"""向量存储配置 - 使用PGVector(需要pgvector扩展)"""

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

_vectorstore = None
_pgvector_available = None


def is_pgvector_available() -> bool:
    """检查pgvector扩展是否可用"""
    global _pgvector_available
    if _pgvector_available is not None:
        return _pgvector_available

    try:
        import psycopg
        conn = psycopg.connect(settings.DATABASE_URL)
        try:
            result = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            _pgvector_available = result.fetchone() is not None
        finally:
            conn.close()
    except Exception:
        _pgvector_available = False

    if not _pgvector_available:
        logger.warning("pgvector扩展不可用，RAG向量检索功能暂不可用")

    return _pgvector_available


def get_vectorstore():
    """获取PGVector向量存储实例

    需要PostgreSQL安装pgvector扩展。
    如果不可用，返回None，知识库工具将使用关键词匹配作为降级方案。
    """
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    if not is_pgvector_available():
        return None

    from langchain_community.vectorstores import PGVector
    from app.rag.embeddings import get_embeddings

    embeddings = get_embeddings()
    _vectorstore = PGVector(
        embedding_function=embeddings,
        connection_string=settings.DATABASE_URL,
        collection_name="knowledge_base",
    )
    return _vectorstore
