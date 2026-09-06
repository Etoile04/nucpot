"""session-provider seam tests(ADR-NFM-4076)。

测试只落在 session-provider 的公开接口上(CONTEXT.md「会话提供者」):
task-scoped 工厂、parse 失败标记、惰性 engine。不探测 engine 内部对象。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import nfm_db.database as database_module
from nfm_db.database import (
    fresh_session,
    mark_parse_failed,
    reset_for_tests,
    task_session_factory,
)
from nfm_db.models.source import DataSource

_PROBE = select(literal(1).label("v"))


@pytest.fixture(autouse=True)
def _sqlite_task_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """task-scoped 工厂从 settings 取连接配置;测试指向一次性 SQLite。

    每个测试结束后复位 provider 缓存:本文件测试构建的 engine 绑定的是
    测试环境 URL,不得泄漏给同进程后面的其他测试文件。
    """
    monkeypatch.setenv("NFM_DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("NFM_DEBUG", "false")
    yield
    reset_for_tests()


class TestTaskScopedFactory:
    """task-scoped 工厂:每个 Celery 任务一把工厂,任务结束不留池状态。

    测试刻意保持为同步函数:它们模拟的正是「无事件循环的 Celery 任务体」
    上下文,每轮任务一个 asyncio.run——与 process_literature_sync 的
    运行形态一致,而不是嵌进 pytest-asyncio 的测试 loop。
    """

    def test_two_consecutive_task_loops_each_get_working_session(self) -> None:
        """同一进程连续两轮 asyncio.run(模拟 prefork worker 的逐任务循环),
        每轮经 task-scoped 工厂拿会话执行查询,互不继承对方的池状态。"""

        async def one_task() -> int:
            async with task_session_factory() as factory:
                async with factory() as session:
                    result = await session.scalar(_PROBE)
                    return int(result)

        assert asyncio.run(one_task()) == 1
        assert asyncio.run(one_task()) == 1

    def test_each_task_receives_a_distinct_factory(self) -> None:
        """BUG-22 不变式的 CI 可见牙齿:若有人把 task 引擎改成共享缓存,
        两个任务拿到同一把工厂,此断言即红——不等 Postgres 才暴露。"""
        seen: list[object] = []

        async def grab() -> None:
            async with task_session_factory() as factory:
                seen.append(factory)

        asyncio.run(grab())
        asyncio.run(grab())
        assert seen[0] is not seen[1]

    def test_engine_disposed_on_exit(self) -> None:
        """任务退出(含异常路径)后 engine 被释放——契约由可观察行为承载:
        异常穿透上下文后,下一轮任务照常工作,没有跨任务残留的坏账。"""

        async def one_task_failing() -> None:
            async with task_session_factory():
                raise RuntimeError("task boom")

        with pytest.raises(RuntimeError, match="task boom"):
            asyncio.run(one_task_failing())

        async def one_task() -> int:
            async with task_session_factory() as factory:
                async with factory() as session:
                    return int(await session.scalar(_PROBE))

        assert asyncio.run(one_task()) == 1


@pytest.mark.skipif(
    not os.environ.get("NFM_TEST_DATABASE_URL"),
    reason="需要真实 Postgres(asyncpg)才能复现 loop-affinity 病灶",
)
class TestTaskScopedFactoryAgainstPostgres:
    """BUG-22 回归(验收不变式 b):共享 engine 的 asyncpg 池绑定首次使用的
    loop,连续 asyncio.run 任务会踩 `Future attached to a different loop`;
    task-scoped NullPool 工厂必须让每一轮独立成功。"""

    def test_bug22_two_consecutive_asyncio_run_tasks(self) -> None:
        async def one_task() -> int:
            async with task_session_factory() as factory:
                async with factory() as session:
                    return int(await session.scalar(_PROBE))

        assert asyncio.run(one_task()) == 1
        assert asyncio.run(one_task()) == 1


class _BrokenFactory:
    """调用即失败的假工厂:模拟连接层彻底不可用。"""

    def __call__(self) -> None:
        raise RuntimeError("connect boom")


class TestMarkParseFailed:
    """parse 失败标记(CONTEXT.md):best-effort 写 DataSource 终态,绝不掩盖原始异常。"""

    async def _engine_with_row(
        self, *, parse_status: str = "parsing"
    ) -> tuple[AsyncSession, ...]:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: DataSource.__table__.create(sync_conn, checkfirst=True)
            )
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        ds_id = uuid.uuid4()
        async with factory() as session:
            session.add(
                DataSource(id=ds_id, title="probe", source_type="file", parse_status=parse_status)
            )
            await session.commit()
        return engine, factory, ds_id

    @pytest.mark.asyncio
    async def test_marks_row_failed_with_truncated_error(self) -> None:
        engine, factory, ds_id = await self._engine_with_row()
        try:
            await mark_parse_failed(ds_id, RuntimeError("x" * 900), session_factory=factory)

            async with factory() as session:
                row = await session.get(DataSource, ds_id)
                assert row is not None
                assert row.parse_status == "failed"
                assert row.parse_error == "x" * 500
                assert row.updated_at is not None
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_unknown_datasource_is_ignored_without_error(self) -> None:
        engine, factory, _ = await self._engine_with_row()
        try:
            await mark_parse_failed(uuid.uuid4(), RuntimeError("x"), session_factory=factory)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_never_raises_when_underlying_write_fails(self) -> None:
        await mark_parse_failed(
            uuid.uuid4(), RuntimeError("original"), session_factory=_BrokenFactory()
        )

    @pytest.mark.asyncio
    async def test_default_path_uses_task_engine(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """不给工厂时,标记经 task-scoped engine 落到 settings 指向的库。"""
        import pathlib

        db_file = pathlib.Path(tmp_path) / "mark.db"  # type: ignore[operator]
        url = f"sqlite+aiosqlite:///{db_file}"
        monkeypatch.setenv("NFM_DATABASE_URL", url)

        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: DataSource.__table__.create(sync_conn, checkfirst=True)
            )
        seed_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        ds_id = uuid.uuid4()
        async with seed_factory() as session:
            session.add(DataSource(id=ds_id, title="probe", source_type="file"))
            await session.commit()
        await engine.dispose()

        await mark_parse_failed(ds_id, RuntimeError("boom"))

        engine2 = create_async_engine(url)
        check = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
        async with check() as session:
            row = await session.get(DataSource, ds_id)
            assert row is not None
            assert row.parse_status == "failed"
            assert row.parse_error == "boom"
        await engine2.dispose()


class TestFreshSession:
    """中毒会话恢复通道(ADR-NFM-4076 D4 / NFM-3322)。"""

    @pytest.mark.asyncio
    async def test_yields_usable_session_without_auto_commit(self) -> None:
        async with fresh_session() as session:
            assert int(await session.scalar(_PROBE)) == 1

    @pytest.mark.asyncio
    async def test_two_fresh_sessions_are_independent(self) -> None:
        async with fresh_session() as s1, fresh_session() as s2:
            assert s1 is not s2


class TestLazyEngine:
    """ADR-NFM-4076 D2:import nfm_db.database 不得构造 engine;首用才建。

    用 AST 静态检查模块体:顶层任何 ``create_async_engine(...)`` 调用
    (即 import 期建引擎的写法)都会被捕获。刻意不在进程内二次导入或
    reload 模块 —— 那会更换模块对象身份、污染同进程后续测试的
    dependency_overrides 匹配(已实证过一次)。
    """

    def test_no_top_level_engine_construction(self) -> None:
        import ast

        source = Path(database_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        offenders: list[int] = []
        for node in tree.body:  # 只看顶层语句
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # 函数/类体内的调用是惰性路径,合法
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    name = getattr(call.func, "id", None) or getattr(
                        call.func, "attr", None
                    )
                    if name == "create_async_engine":
                        offenders.append(node.lineno)

        assert offenders == [], f"import 期构造 engine 的顶层调用:行 {offenders}"
