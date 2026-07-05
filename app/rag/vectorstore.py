"""向量存储配置 - 使用PGVector"""

from langchain_community.vectorstores import PGVector

from app.core.config import settings
from app.rag.embeddings import get_embeddings


def get_vectorstore() -> PGVector:
    """获取PGVector向量存储实例

    使用与Checkpointer/Store相同的PostgreSQL实例，
    减少运维复杂度。pgvector扩展已内置在docker镜像中。
    """
    embeddings = get_embeddings()
    return PGVector(
        embedding_function=embeddings,
        connection_string=settings.DATABASE_URL,
        collection_name="knowledge_base",
    )
