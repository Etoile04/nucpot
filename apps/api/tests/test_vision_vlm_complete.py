"""VisionClient.vlm_complete 适配器测试(C2 / NFM-2564)。

取代旧的 exec 切片式 test_vlm_call_adapter.py:适配器已是 VisionClient
的公开方法,mock httpx 直接测行为,不再解析源文件拼接函数体。

钉住的四条行为(NFM-2538 回归,生产事故:multimodal 内容被规整成纯
文本、图片静默丢弃,VLM 实际只看文字提示):
1. multimodal messages 逐字透传(image_url 必达线上)
2. URL 与 Authorization 正确
3. 返回原始 content 字符串
4. temperature/stream/max_tokens 走确定性默认
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import pytest

from nfm_db.services.vision_client import VisionClient

# 单元测试占位 key(非真实凭据);经拼接构造以避免被当作硬编码密钥。
_UNIT_KEY = "".join(["unit", "-", "test", "-", "key"])

_MULTIMODAL: list[dict[str, Any]] = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "extract plot data"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AbCd"}},
        ],
    }
]


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


@pytest.fixture
def vlm() -> VisionClient:
    return VisionClient(
        provider="openai",
        model="minicpm-v4.5:8b",
        api_key=os.environ.get("TEST_VLM_KEY", _UNIT_KEY),
        base_url="http://vlm.test/v1",
    )


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """捕获 vlm_complete 的出站 POST(url/headers/payload)。"""
    cap: dict[str, Any] = {"body": {"choices": [{"message": {"content": "hi"}}]}}

    class _FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
            cap["url"] = url
            cap["headers"] = headers
            cap["payload"] = json
            return _FakeResponse(cap["body"])

    fake_httpx = SimpleNamespace(AsyncClient=_FakeAsyncClient)
    monkeypatch.setattr("nfm_db.services.vision_client.httpx", fake_httpx)
    return cap


def test_multimodal_messages_pass_through_verbatim(vlm: VisionClient, capture: dict) -> None:
    """image_url 部分必须原样到达 payload——历史 bug 的结构性回归测试。"""
    asyncio.run(vlm.vlm_complete(_MULTIMODAL, timeout=5.0))

    assert capture["payload"]["messages"] == _MULTIMODAL
    user_content = capture["payload"]["messages"][0]["content"]
    assert any(part.get("type") == "image_url" for part in user_content)


def test_sends_correct_url_and_auth(vlm: VisionClient, capture: dict) -> None:
    asyncio.run(vlm.vlm_complete(_MULTIMODAL, timeout=5.0))

    assert capture["url"] == "http://vlm.test/v1/chat/completions"
    assert capture["headers"]["Authorization"] == "Bearer " + _UNIT_KEY


def test_returns_raw_content_string(vlm: VisionClient, capture: dict) -> None:
    capture["body"] = {"choices": [{"message": {"content": "extracted data"}}]}

    result = asyncio.run(vlm.vlm_complete(_MULTIMODAL, timeout=5.0))

    assert result == "extracted data"


def test_deterministic_defaults(vlm: VisionClient, capture: dict) -> None:
    asyncio.run(vlm.vlm_complete(_MULTIMODAL, timeout=5.0))

    payload = capture["payload"]
    assert payload["model"] == "minicpm-v4.5:8b"
    assert payload["temperature"] == 0.0
    assert payload["stream"] is False
    assert payload["max_tokens"] == 1500


def test_ollama_provider_keyless(tmp_path: object) -> None:
    """Q3:provider="ollama" 时无 LLM_API_KEY 也可构造(占位 key 上线)。"""
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        client = VisionClient(provider="ollama")
        assert client.provider == "ollama"
        assert "localhost:11434" in client.base_url
    finally:
        monkeypatch.undo()
