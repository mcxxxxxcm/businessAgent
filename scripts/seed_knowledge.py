"""知识库数据导入脚本 - 将模拟知识导入PGVector"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools.knowledge_rag import _MOCK_KNOWLEDGE
from app.rag.loader import load_knowledge_documents, create_documents_from_knowledge


async def seed_knowledge_base():
    """将模拟知识库数据导入PGVector向量存储"""
    print("开始导入知识库数据...")

    # 将模拟数据转换为Document格式
    documents = create_documents_from_knowledge(_MOCK_KNOWLEDGE)

    print(f"共 {len(documents)} 条知识条目待导入")

    try:
        await load_knowledge_documents(documents)
        print("知识库导入完成！")
    except Exception as e:
        print(f"导入失败: {e}")
        print("提示: 请确保PostgreSQL和pgvector扩展已正确安装")
        print("运行 python scripts/init_db.py 先初始化数据库")


if __name__ == "__main__":
    asyncio.run(seed_knowledge_base())
