"""任务仓储：下载任务的 CRUD 操作。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from teleharvest.db.models import Task
from teleharvest.utils.time import now_utc

if TYPE_CHECKING:
    from teleharvest.db.session import DatabaseSession


class TaskRepository:
    """下载任务数据访问。"""

    def __init__(self, db: DatabaseSession) -> None:
        self._db = db

    async def create(self, task: Task) -> Task:
        """创建任务。"""
        async with self._db.session() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def get_by_id(self, task_id: int) -> Task | None:
        """按 ID 查询任务。"""
        async with self._db.session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id))
            return result.scalar_one_or_none()

    async def get_pending(self, limit: int = 10) -> list[Task]:
        """获取待执行任务。"""
        async with self._db.session() as session:
            result = await session.execute(
                select(Task).where(Task.status == "pending").order_by(Task.created_at).limit(limit)
            )
            return list(result.scalars().all())

    async def update_status(
        self,
        task_id: int,
        status: str,
        error: str = "",
        downloaded_bytes: int | None = None,
    ) -> None:
        """更新任务状态。"""
        values: dict[str, Any] = {"status": status}
        if error:
            values["error"] = error
        if downloaded_bytes is not None:
            values["downloaded_bytes"] = downloaded_bytes
        if status in ("done", "failed", "cancelled"):
            values["finished_at"] = now_utc()
        elif status == "running":
            values["started_at"] = now_utc()

        async with self._db.session() as session:
            await session.execute(update(Task).where(Task.id == task_id).values(**values))
            await session.commit()
