"""gap_literature 任务的 seam 测试(ADR-NFM-4076 D1 Phase B / T3,#1180)。

任务体是 Celery prefork 下的 ``asyncio.run`` 逐任务执行——正是 BUG-22 的
病灶形态。T3 把会话获取迁到 provider 的 task-scoped 通道后,每个任务的
每个事件循环各自持有 NullPool engine,互不继承。

测试用 tmp 文件 SQLite 承载请求/文献行:task-scoped 工厂在任务执行时
从 settings 解析 URL,因此指向测试库的行才可能被任务真实改写。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.models.data_collection_request import DataCollectionRequest
from nfm_db.models.source import DataSource


@pytest.fixture()
def task_db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """任务引擎经 settings 取 URL;指向一次性 tmp 文件库。"""
    url = f"sqlite+aiosqlite:///{tmp_path}/gap.db"
    monkeypatch.setenv("NFM_DATABASE_URL", url)
    monkeypatch.setenv("NFM_DEBUG", "false")
    return url


@pytest.fixture(autouse=True)
def _no_crossref(monkeypatch: pytest.MonkeyPatch) -> None:
    """Crossref 是外部 HTTP;按用例需要打桩为无命中/固定 DOI。"""
    import nfm_db.tasks.gap_literature_task as task_mod

    monkeypatch.setattr(task_mod, "_search_crossref", lambda m, p: None)


async def _make_tables_and_request(url: str) -> uuid.UUID:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: DataCollectionRequest.__table__.create(c, checkfirst=True)
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    req_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            DataCollectionRequest(
                id=req_id,
                ontology_version_id=uuid.uuid4(),
                entity_type="potential",
                property="cohesive_energy",
                material_system="Fe",
            )
        )
        await session.commit()
    await engine.dispose()
    return req_id


async def _request_status(url: str, req_id: uuid.UUID) -> str | None:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        row = await session.get(DataCollectionRequest, req_id)
        return row.status if row is not None else None


def test_crossref_miss_marks_request_failed_without_raising(task_db_url: str) -> None:
    """AC:Crossref 无命中 → 请求行 failed、任务返回 failed、不抛出。"""
    from nfm_db.tasks.gap_literature_task import process_gap_literature_task

    req_id = asyncio.run(_make_tables_and_request(task_db_url))

    result = process_gap_literature_task(str(req_id), "potential", "cohesive_energy", "Fe")

    assert result["status"] == "failed"
    assert asyncio.run(_request_status(task_db_url, req_id)) == "failed"


def test_two_consecutive_task_runs_are_independent(task_db_url: str) -> None:
    """BUG-22 CI 牙齿:同一进程连续两轮任务(各自 asyncio.run)互不继承
    池状态——第二轮照常改写自己的请求行。"""
    from nfm_db.tasks.gap_literature_task import process_gap_literature_task

    req_one = asyncio.run(_make_tables_and_request(task_db_url))
    req_two = asyncio.run(_make_tables_and_request(task_db_url))

    first = process_gap_literature_task(str(req_one), "potential", "cohesive_energy", "Fe")
    second = process_gap_literature_task(str(req_two), "potential", "cohesive_energy", "Fe")

    assert first["status"] == "failed"
    assert second["status"] == "failed"
    assert asyncio.run(_request_status(task_db_url, req_one)) == "failed"
    assert asyncio.run(_request_status(task_db_url, req_two)) == "failed"


def test_ingest_happy_path_uses_task_engine_and_completes_request(
    task_db_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主路径:Crossref 命中 → DOI 入库 + 请求 completed,全程任务引擎。

    这是潜伏 BUG-22 的真正载体:ingest 与状态回写原先都跑在共享工厂上。
    """
    import nfm_db.tasks.gap_literature_task as task_mod
    from nfm_db.tasks.gap_literature_task import process_gap_literature_task

    doi = "10.1234/gap-filler"
    monkeypatch.setattr(task_mod, "_search_crossref", lambda m, p: doi)

    def _fake_fetch(d: str) -> str:
        return f"# paper {d}\n\ncohesive energy data"

    monkeypatch.setattr("nfm_db.services.doi_fetcher.fetch_paper_content", _fake_fetch)

    class _TmpStorage:
        def __init__(self, root: Path) -> None:
            self._root = root

        def save(self, datasource_id: Any, filename: str, content: bytes) -> str:
            path = self._root / f"{datasource_id}-{filename}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return str(path)

    monkeypatch.setattr(
        "nfm_db.services.storage.get_storage", lambda: _TmpStorage(tmp_path / "storage")
    )

    dispatched: list[Any] = []
    monkeypatch.setattr(
        "nfm_db.services.literature_dispatcher.schedule_literature_processing",
        lambda literature_id: dispatched.append(literature_id),
    )

    async def _make_tables_and_request_with_sources(url: str) -> uuid.UUID:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: DataCollectionRequest.__table__.create(c, checkfirst=True)
            )
            await conn.run_sync(lambda c: DataSource.__table__.create(c, checkfirst=True))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        req_id = uuid.uuid4()
        async with factory() as session:
            session.add(
                DataCollectionRequest(
                    id=req_id,
                    ontology_version_id=uuid.uuid4(),
                    entity_type="potential",
                    property="cohesive_energy",
                    material_system="Fe",
                )
            )
            await session.commit()
        await engine.dispose()
        return req_id

    req_id = asyncio.run(_make_tables_and_request_with_sources(task_db_url))

    result = process_gap_literature_task(str(req_id), "potential", "cohesive_energy", "Fe")

    assert result["status"] == "completed"
    assert result["doi"] == doi
    assert len(dispatched) == 1
    assert asyncio.run(_request_status(task_db_url, req_id)) == "completed"

    async def _literature_count(url: str) -> int:
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            rows = (await session.scalars(select(DataSource))).all()
            return len(rows)

    assert asyncio.run(_literature_count(task_db_url)) == 1
