"""PostgreSQL AsyncPostgresSaver工厂 - 短期记忆(对话持久化)

包含:
- 竞态安全的单例初始化
- Checkpoint created_at 列自动迁移
- 双策略定期清理:
  1. keep_latest: 每线程只保留最新checkpoint
  2. delete_after_ttl: 删除N天未活跃的整个线程
"""

import asyncio
import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings
from app.core._async_utils import async_init_singleton

logger = logging.getLogger(__name__)

_checkpointer: AsyncPostgresSaver | None = None


async def _create_checkpointer() -> AsyncPostgresSaver:
    """创建并初始化AsyncPostgresSaver实例"""
    from app.core.postgres import get_pg_pool

    pool = await get_pg_pool()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    # 添加 created_at 列用于TTL清理(幂等操作)
    try:
        async with pool.connection() as conn:
            await conn.execute(
                "ALTER TABLE checkpoints ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS checkpoints_created_at_idx ON checkpoints(created_at)"
            )
    except Exception:
        pass  # 列已存在或其他兼容性问题，不阻塞启动

    return checkpointer


async def get_checkpointer() -> AsyncPostgresSaver:
    """获取AsyncPostgresSaver实例(单例，竞态安全)"""
    return await async_init_singleton(globals(), "_checkpointer", _create_checkpointer)


# ============================================================
# Checkpoint 定期清理
# ============================================================

_cleanup_task: asyncio.Task | None = None
_cleanup_stop_event = asyncio.Event()


async def cleanup_old_checkpoints() -> dict:
    """清理旧的checkpoint数据

    双策略组合:
    1. keep_latest: 每个线程只保留最新checkpoint，删除旧的
    2. delete_after_ttl: 删除超过N天未活跃的整个线程

    Returns:
        {"deleted_checkpoints": int, "deleted_threads": int, "errors": list}
    """
    from app.core.postgres import get_pg_pool

    pool = await get_pg_pool()
    result = {"deleted_checkpoints": 0, "deleted_threads": 0, "errors": []}

    try:
        async with pool.connection() as conn:
            batch_size = settings.CHECKPOINT_CLEANUP_BATCH_SIZE

            # === 策略1: keep_latest — 删除每个线程的非最新checkpoint ===
            if settings.CHECKPOINT_KEEP_LATEST:
                # Step 1: 删除 checkpoint_writes (子表)
                await conn.execute(
                    """
                    DELETE FROM checkpoint_writes
                    WHERE (thread_id, checkpoint_ns, checkpoint_id) IN (
                        SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id
                        FROM checkpoints c
                        INNER JOIN (
                            SELECT thread_id, checkpoint_ns, MAX(checkpoint_id) AS latest_id
                            FROM checkpoints
                            GROUP BY thread_id, checkpoint_ns
                        ) latest ON c.thread_id = latest.thread_id
                                  AND c.checkpoint_ns = latest.checkpoint_ns
                        WHERE c.checkpoint_id != latest.latest_id
                        LIMIT %s
                    )
                    """,
                    (batch_size,),
                )

                # Step 2: 删除 checkpoint_blobs
                await conn.execute(
                    """
                    DELETE FROM checkpoint_blobs
                    WHERE (thread_id, checkpoint_ns, version) IN (
                        SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id
                        FROM checkpoints c
                        INNER JOIN (
                            SELECT thread_id, checkpoint_ns, MAX(checkpoint_id) AS latest_id
                            FROM checkpoints
                            GROUP BY thread_id, checkpoint_ns
                        ) latest ON c.thread_id = latest.thread_id
                                  AND c.checkpoint_ns = latest.checkpoint_ns
                        WHERE c.checkpoint_id != latest.latest_id
                        LIMIT %s
                    )
                    """,
                    (batch_size,),
                )

                # Step 3: 删除 checkpoints (主表)
                row = await conn.execute(
                    """
                    DELETE FROM checkpoints
                    WHERE (thread_id, checkpoint_ns, checkpoint_id) IN (
                        SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id
                        FROM checkpoints c
                        INNER JOIN (
                            SELECT thread_id, checkpoint_ns, MAX(checkpoint_id) AS latest_id
                            FROM checkpoints
                            GROUP BY thread_id, checkpoint_ns
                        ) latest ON c.thread_id = latest.thread_id
                                  AND c.checkpoint_ns = latest.checkpoint_ns
                        WHERE c.checkpoint_id != latest.latest_id
                        LIMIT %s
                    )
                    """,
                    (batch_size,),
                )
                # 解析 "DELETE N" 的行数
                deleted = int(row.split()[-1]) if row else 0
                result["deleted_checkpoints"] = deleted

            # === 策略2: delete_after_ttl — 删除超期线程 ===
            ttl_days = settings.CHECKPOINT_THREAD_TTL_DAYS
            if ttl_days > 0:
                # 找出最新checkpoint的created_at超过TTL天的thread_id
                # 删除这些线程的全部数据(三表联动)
                expired_row = await conn.execute(
                    """
                    DELETE FROM checkpoints
                    WHERE thread_id IN (
                        SELECT thread_id
                        FROM checkpoints
                        GROUP BY thread_id, checkpoint_ns
                        HAVING MAX(created_at) < NOW() - INTERVAL '%s days'
                        LIMIT %s
                    )
                    """,
                    (ttl_days, batch_size),
                )
                expired_count = int(expired_row.split()[-1]) if expired_row else 0
                result["deleted_threads"] = expired_count

                # 同步清理子表中的超期线程数据
                await conn.execute(
                    """
                    DELETE FROM checkpoint_writes
                    WHERE thread_id NOT IN (SELECT DISTINCT thread_id FROM checkpoints)
                    """,
                )
                await conn.execute(
                    """
                    DELETE FROM checkpoint_blobs
                    WHERE thread_id NOT IN (SELECT DISTINCT thread_id FROM checkpoints)
                    """,
                )

            if result["deleted_checkpoints"] > 0 or result["deleted_threads"] > 0:
                logger.info(
                    "Checkpoint清理完成: 删除%d条旧checkpoint, %d条超期线程",
                    result["deleted_checkpoints"],
                    result["deleted_threads"],
                )

    except Exception as e:
        logger.error("Checkpoint清理失败: %s", e)
        result["errors"].append(str(e))

    return result


async def start_checkpoint_cleanup_task() -> asyncio.Task:
    """启动checkpoint定期清理后台任务"""
    global _cleanup_task, _cleanup_stop_event
    _cleanup_stop_event.clear()

    interval = settings.CHECKPOINT_CLEANUP_INTERVAL_MINUTES * 60  # 转秒

    async def _cleanup_loop():
        logger.info(
            "Checkpoint清理任务已启动, 间隔=%d分钟",
            settings.CHECKPOINT_CLEANUP_INTERVAL_MINUTES,
        )
        while not _cleanup_stop_event.is_set():
            # 等待间隔时间，可被stop_event中断
            try:
                await asyncio.wait_for(
                    _cleanup_stop_event.wait(),
                    timeout=interval,
                )
                break  # stop_event被设置，退出
            except asyncio.TimeoutError:
                pass  # 超时意味着该执行清理了

            try:
                await cleanup_old_checkpoints()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("定期清理异常: %s", e)

    _cleanup_task = asyncio.create_task(_cleanup_loop())
    _cleanup_task.set_name("checkpoint_cleanup")
    return _cleanup_task


async def stop_checkpoint_cleanup_task() -> None:
    """停止checkpoint清理后台任务"""
    global _cleanup_task, _cleanup_stop_event
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_stop_event.set()
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None
        logger.info("Checkpoint清理任务已停止")
