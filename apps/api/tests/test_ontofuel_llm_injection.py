"""ontofuel_extract 的 LLM 注入测试(C2 / NFM-2564 Q2 方案 a)。

注入一个与 ``call_llm`` 同签名的 async callable,即可离线跑通抽取、
绕过环境 LLM 门;缺省路径行为不变(无注入且未配 key → stub 回退)。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import OntologyVersion, User
from nfm_db.services.extraction_pipeline import ontofuel_extract
from nfm_db.services.llm_client import _get_config

_FAKE_PROPERTIES: list[dict[str, Any]] = [
    {
        "element_system": "UO2",
        "phase": "FCC",
        "property_name": "lattice_constant",
        "value": 5.47,
        "unit": "angstrom",
        "method": "DFT",
        "source_doi": None,
        "confidence": "high",
        "uncertainty": 0.01,
        "temperature": 300.0,
    }
]

_ONTOLOGY_DATA: dict[str, Any] = {
    "entity_types": [
        {
            "name": "NuclearFuel",
            "description": "Base class for nuclear fuels",
            "required_properties": ["name", "composition"],
        },
    ],
    "relation_types": [],
}


@pytest.fixture(autouse=True)
def _no_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 LLM_API_KEY、无 stub 模式:环境门只对缺省栈生效。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EXTRACTION_STUB_MODE", raising=False)


def _write_markdown(tmp_path: Any, content: str) -> str:
    path = tmp_path / "paper.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


async def _fake_llm(**kwargs: Any) -> list[dict[str, Any]]:
    """与 call_llm 同签名的离线假实现:记录 prompt,返回固定属性。"""
    return [dict(_FAKE_PROPERTIES[0], prompted_chars=len(kwargs.get("user_message", "")))]


async def _seed_published_ontology(db_session: AsyncSession) -> None:
    """自建 user + 已发布本体版本(不依赖 conftest 的播种常量)。"""
    user = User(
        username=f"onto-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="hashed",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        OntologyVersion(
            version=f"1.0.{uuid.uuid4().int % 1000}",
            status="published",
            created_by=user.id,
            ontology_data=_ONTOLOGY_DATA,
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_injected_llm_call_runs_offline_and_bypasses_env_gate(
    tmp_path: Any,
    db_session: AsyncSession,
) -> None:
    """注入 llm_call:无 LLM_API_KEY 环境下也走注入实现,而非 stub 回退。"""
    await _seed_published_ontology(db_session)

    source = _write_markdown(tmp_path, "# paper\n\ncohesive energy of UO2 is 5.47 eV")

    calls: list[dict[str, Any]] = []

    async def fake_llm(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [dict(_FAKE_PROPERTIES[0])]

    result = await ontofuel_extract(
        source,
        "other",
        db=db_session,
        llm_call=fake_llm,
    )

    assert len(calls) == 1  # 单 chunk 一次调用
    assert result, "注入路径必须产出(后处理后的)属性列表"
    names = {row.get("property_name") for row in result}
    assert "lattice_constant" in names


@pytest.mark.asyncio
async def test_default_stack_still_falls_back_to_stub_without_key(
    tmp_path: Any,
) -> None:
    """缺省(不注入)+ 无 key:保持既有 stub 回退行为不变。"""
    source = _write_markdown(tmp_path, "# paper\n\ncohesive energy data")

    result = await ontofuel_extract(source, "other")

    assert result, "stub 回退应产出占位属性"
    assert {row.get("element_system") for row in result} == {"UO2"}


def test_get_config_resolves_ollama_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q3 完整性:LLM_PROVIDER=ollama 时旧栈同样获得 ollama 端点与占位 key。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    cfg = _get_config()

    assert cfg["base_url"] == "http://localhost:11434/v1"
    assert cfg["api_key"] == "ollama"


# ---------------------------------------------------------------------------
# NFM-4730-FixB — per-chunk dispatcher passes num_predict + enable_thinking=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ontofuel_extract_passes_num_predict_and_disable_thinking(
    tmp_path: Any,
    db_session: AsyncSession,
) -> None:
    """NFM-4730-FixB: per-chunk call site must pass num_predict=8192 + chat_template_kwargs={'enable_thinking': False}.

    Without these overrides the qwen3.5:4b-nvfp4 dispatcher burns the
    entire 16K output budget on thinking-mode reasoning and returns
    finish_reason=length with empty content. See NFM-4525 for why the
    chat_template_kwargs flag is a no-op against the openai-compat layer
    but is included for forward-compat with a future native-Ollama binding.
    """
    await _seed_published_ontology(db_session)
    source = _write_markdown(
        tmp_path,
        "# paper\n\ncohesive energy of UO2 is 5.47 eV",
    )

    calls: list[dict[str, Any]] = []

    async def fake_llm(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [dict(_FAKE_PROPERTIES[0])]

    await ontofuel_extract(
        source,
        "other",
        db=db_session,
        llm_call=fake_llm,
    )

    assert calls, "injected llm_call must be invoked at least once"
    kwargs = calls[0]
    assert kwargs.get("num_predict") == 8192, (
        f"per-chunk num_predict must be 8192, got {kwargs.get('num_predict')!r}"
    )
    assert kwargs.get("chat_template_kwargs") == {"enable_thinking": False}, (
        "per-chunk chat_template_kwargs must disable thinking mode"
    )
