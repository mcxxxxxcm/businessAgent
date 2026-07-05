"""RAG检索器构建 - 用于知识库Agent"""

from langchain_community.vectorstores import PGVector

from app.rag.vectorstore import get_vectorstore


def get_knowledge_retriever():
    """获取知识库检索器

    使用MMR(Maximal Marginal Relevance)搜索策略，
    兼顾相关性和多样性。
    """
    vectorstore = get_vectorstore()
    return vectorstore.as_retriever(
        search_type="mmr",
        search_kwargs={"k": 3, "fetch_k": 10},
    )
