"""ingest_service 的 service 级测试(C3 / NFM-2564)。

领域算法脱离 HTTP 直接测试:AC-R3 sync-verification(说谎 mapper)、
AC-5 语料规则、AC-6 批量上限。全部离线(SQLite in-memory + 注入
mapper),不需要 HTTP 客户端,也不 patch 模块属性。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from nfm_db.models import Corpus
from nfm_db.models.user import User
from nfm_db.schemas.extraction import ExtractionIngestRequest
from nfm_db.services.ingest_service import (
    BatchTooLargeError,
    CorpusNotRegisteredError,
    ingest_extraction_batch,
)

_REF_DOI = "10.1234/ingest-service"


def _payload(**overrides: Any) -> ExtractionIngestRequest:
    base: dict[str, Any] = {
        "source_reference": _REF_DOI,
        "source_type": "doi",
        "corpus_id": "corpus-svc-test",
        "properties": [{"property": "lattice_constant", "value": 5.47}],
    }
    base.update(overrides)
    return ExtractionIngestRequest(**base)


def _service_account() -> User:
    return User(
        username=f"svc-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="hashed",
        is_service_account=True,
    )


def _human() -> User:
    return User(
        username=f"human-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="hashed",
    )


def _mapper_result(claimed: int) -> SimpleNamespace:
    """map_and_persist 返回值的鸭子类型;claimed 由用例指定(可说谎)。"""
    return SimpleNamespace(
        created_measurements=claimed,
        reused_entities=0,
        skipped_duplicate_measurements=0,
        skipped_unknown_properties=0,
        skipped_unknown_materials=0,
        skipped_duplicates=0,
        validation_errors=0,
    )


@pytest.mark.asyncio
async def test_service_account_auto_creates_corpus(db_session: Any) -> None:
    """AC-5:服务账号 + 语料不存在 → 自动创建(is_auto_created=True)。"""
    caller = _service_account()

    ack = await ingest_extraction_batch(db_session, _payload(), caller)

    assert ack.corpus_id == "corpus-svc-test"
    assert ack.job_id is not None
    corpus = await db_session.scalar(
        select(Corpus).where(Corpus.corpus_id == "corpus-svc-test")
    )
    assert corpus is not None
    assert corpus.is_auto_created is True


@pytest.mark.asyncio
async def test_human_caller_unknown_corpus_raises_domain_error(db_session: Any) -> None:
    """AC-5:人工调用者 + 语料不存在 → 领域错误(路由翻译为 400)。"""
    with pytest.raises(CorpusNotRegisteredError, match="corpus 'c-x' not registered"):
        await ingest_extraction_batch(db_session, _payload(corpus_id="c-x"), _human())


@pytest.mark.asyncio
async def test_batch_over_cap_raises_domain_error(db_session: Any) -> None:
    """AC-6:超过 500 条 → BatchTooLargeError(路由翻译为 400)。"""
    big = _payload(properties=[{"property": f"p{i}"} for i in range(501)])

    with pytest.raises(BatchTooLargeError, match="maximum of 500"):
        await ingest_extraction_batch(db_session, big, _service_account())


@pytest.mark.asyncio
async def test_lying_mapper_fails_sync_verification(db_session: Any) -> None:
    """AC-R3 核心不变式:声称 created=2 但一行未写 → verified=False +
    drift 错误进入 ack.errors,且 ExtractionJob 落库留痕。"""
    async def lying_mapper(session: Any, props: list[dict[str, Any]]) -> Any:
        return _mapper_result(claimed=2)  # 说谎:什么都没写

    ack = await ingest_extraction_batch(
        db_session, _payload(), _service_account(), mapper=lying_mapper
    )

    assert ack.verified is False
    assert ack.db_measurement_count == 0
    assert any("sync-verification MISMATCH" in e for e in ack.errors)


@pytest.mark.asyncio
async def test_honest_zero_created_mapper_is_verified(db_session: Any) -> None:
    """诚实的 mapper(claimed=0 且确实零写入)→ verified=True。"""
    async def honest_mapper(session: Any, props: list[dict[str, Any]]) -> Any:
        return _mapper_result(claimed=0)

    ack = await ingest_extraction_batch(
        db_session, _payload(), _service_account(), mapper=honest_mapper
    )

    assert ack.verified is True
    assert ack.errors == []
