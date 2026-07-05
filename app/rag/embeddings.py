"""Embedding模型配置 - 用于知识库向量检索"""

from langchain_community.embeddings import DashScopeEmbeddings

from app.core.config import settings


def get_embeddings() -> DashScopeEmbeddings:
    """获取Embedding模型实例

    使用阿里云DashScope的text-embedding-v2模型，
    中文效果好，价格低。
    生产环境可替换为其他Embedding模型。
    """
    return DashScopeEmbeddings(
        model="text-embedding-v2",
        dashscope_api_key=settings.ZHIPU_API_KEY,  # DashScope和智谱共享API Key
    )
