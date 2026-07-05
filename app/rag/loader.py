"""知识文档加载器 - 将文档导入向量存储"""

import logging

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.vectorstore import get_vectorstore

logger = logging.getLogger(__name__)


async def load_knowledge_documents(documents: list[Document]) -> None:
    """将知识文档导入PGVector向量存储

    Args:
        documents: 文档列表，每个文档包含page_content和metadata
    """
    vectorstore = get_vectorstore()

    # 文档切分
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", "。", "！", "？", ".", " "],
    )

    splits = text_splitter.split_documents(documents)

    # 导入向量存储
    await vectorstore.aadd_documents(splits)

    logger.info("知识文档导入完成: %d documents, %d splits", len(documents), len(splits))


def create_documents_from_knowledge(knowledge_items: list[dict]) -> list[Document]:
    """将知识条目转换为LangChain Document格式

    Args:
        knowledge_items: 知识条目列表，每个条目包含title/category/content

    Returns:
        Document列表
    """
    documents = []
    for item in knowledge_items:
        doc = Document(
            page_content=item["content"],
            metadata={
                "title": item["title"],
                "category": item.get("category", "general"),
            },
        )
        documents.append(doc)
    return documents
