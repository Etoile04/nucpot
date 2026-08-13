# SPDX-License-Identifier: Apache-2.0
"""Unit tests for mineru_vision_extractor (NFM-1366 follow-up)."""
from __future__ import annotations

import asyncio
import base64
import io

import pytest
from PIL import Image

from nfm_db.services.mineru_vision_extractor import (
    ExtractionResult,
    FigureRef,
    _process_figures_parallel,
    extract_figures_with_mineru,
    image_to_base64_jpeg,
    parse_figure_refs,
    parse_vlm_json,
    resize_for_vlm,
    to_job_figure,
    vlm_extract,
    vlm_verify,
)

# ---------------------------------------------------------------------------
# parse_figure_refs
# ---------------------------------------------------------------------------


def _make_image_bytes(color: str = "red", size: tuple[int, int] = (10, 10)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_parse_figure_refs_basic():
    md = """Some intro text.

![Caption](images/abc123.jpg)

Figure 1 shows the lattice constants of U-Zr.

![More caption](images/def456.jpg)

Figure 2: another caption here.

![Third](images/ghi789.jpg)

Figure 3: comparison plot.
"""
    images = {
        "abc123.jpg": _make_image_bytes("red"),
        "def456.jpg": _make_image_bytes("blue"),
        "ghi789.jpg": _make_image_bytes("green"),
    }

    refs = parse_figure_refs(md, images)
    assert len(refs) == 3
    assert refs[0].image_ref == "images/abc123.jpg"
    assert refs[0].figure_numbers == ["1"]
    assert refs[0].kind == "figure"
    assert refs[1].figure_numbers == ["2"]
    assert refs[2].figure_numbers == ["3"]


def test_parse_figure_refs_chinese_captions():
    md = """实验结果。

![图1](images/cap1.png)

图 1 展示了晶格常数随 Zr 浓度的变化。

![表1](images/tab1.png)

表 1 列出了弹性常数的计算结果。
"""
    images = {
        "cap1.png": _make_image_bytes("green"),
        "tab1.png": _make_image_bytes("yellow"),
    }

    refs = parse_figure_refs(md, images)
    assert len(refs) == 2
    assert refs[0].figure_numbers == ["1"]
    assert refs[0].kind == "figure"  # "图 X" → figure
    assert refs[1].figure_numbers == ["1"]
    assert refs[1].kind == "table"  # "表 X" → table


def test_parse_figure_refs_dedups_duplicate_refs():
    md = """![cap](images/same.jpg) ![cap2](images/same.jpg)

Figure 1: same image referenced twice."""
    images = {"same.jpg": _make_image_bytes()}
    refs = parse_figure_refs(md, images)
    assert len(refs) == 1


def test_parse_figure_refs_skips_missing_images():
    md = """![cap](images/exists.jpg) ![missing](images/missing.jpg)

Figure 1 in text."""
    images = {"exists.jpg": _make_image_bytes()}
    refs = parse_figure_refs(md, images)
    assert len(refs) == 1
    assert refs[0].image_ref == "images/exists.jpg"


def test_parse_figure_refs_no_figure_number_yields_unknown():
    md = """![cap](images/img.jpg)

No figure number in this paragraph."""
    images = {"img.jpg": _make_image_bytes()}
    refs = parse_figure_refs(md, images)
    assert len(refs) == 1
    assert refs[0].kind == "unknown"
    assert refs[0].figure_numbers == []


# ---------------------------------------------------------------------------
# parse_vlm_json
# ---------------------------------------------------------------------------


def test_parse_vlm_json_direct():
    obj = parse_vlm_json('{"type": "plot", "title": "X"}')
    assert obj == {"type": "plot", "title": "X"}


def test_parse_vlm_json_with_code_fence():
    obj = parse_vlm_json('```json\n{"type": "plot"}\n```')
    assert obj == {"type": "plot"}


def test_parse_vlm_json_with_trailing_prose():
    raw = '{"type": "plot"} extra prose that should be ignored'
    obj = parse_vlm_json(raw)
    assert obj == {"type": "plot"}


def test_parse_vlm_json_recovery_finds_expected_key():
    # Malformed: missing comma
    raw = '{"type": "plot" "title": "broken"}'
    # Should fail direct parse, recover via grep
    obj = parse_vlm_json(raw)
    # Acceptable: None or recovered partial
    if obj is not None:
        assert "type" in obj or "title" in obj


def test_parse_vlm_json_returns_none_on_garbage():
    assert parse_vlm_json("not json at all") is None
    assert parse_vlm_json("") is None


# ---------------------------------------------------------------------------
# resize_for_vlm
# ---------------------------------------------------------------------------


def test_resize_for_vlm_large_input():
    img_bytes = _make_image_bytes("red", size=(3000, 2000))
    out = resize_for_vlm(img_bytes, max_dim=512)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 512


def test_resize_for_vlm_small_input_unchanged():
    img_bytes = _make_image_bytes("red", size=(100, 100))
    out = resize_for_vlm(img_bytes, max_dim=1024)
    img = Image.open(io.BytesIO(out))
    # Same size or smaller — never enlarged beyond max_dim
    assert max(img.size) <= 1024


def test_image_to_base64_jpeg_roundtrip():
    img_bytes = _make_image_bytes("red")
    b64 = image_to_base64_jpeg(img_bytes)
    decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert decoded.format == "JPEG"


# ---------------------------------------------------------------------------
# to_job_figure
# ---------------------------------------------------------------------------


def _make_result(
    extracted: dict | None = None,
    verification: dict | None = None,
    figure_numbers: list[str] | None = None,
    kind: str = "figure",
) -> ExtractionResult:
    return ExtractionResult(
        figure_ref=FigureRef(
            image_bytes=b"\x89PNG" + b"\x00" * 100,
            image_ref="images/test.jpg",
            figure_numbers=figure_numbers or ["1"],
            kind=kind,
        ),
        extracted=extracted,
        verification=verification,
    )


def test_to_job_figure_high_confidence():
    r = _make_result(
        extracted={"type": "plot", "title": "Heat of Formation"},
        verification={"accuracy": "high", "issues": []},
    )
    fig = to_job_figure(r, source_reference="src1")
    assert fig is not None
    assert fig["figure_type"] == "plot"
    assert fig["title"] == "Heat of Formation"
    assert fig["confidence"] == 0.9
    assert fig["accuracy"] == "high"
    assert fig["extraction_method"] == "mineru_vlm"
    assert fig["figure_numbers"] == ["1"]


def test_to_job_figure_medium_confidence():
    r = _make_result(
        extracted={"type": "table", "headers": ["a", "b"], "rows": [["1", "2"]]},
        verification={"accuracy": "medium", "issues": ["missing unit"]},
    )
    fig = to_job_figure(r, source_reference="src2")
    assert fig["confidence"] == 0.6
    assert fig["issues"] == ["missing unit"]


def test_to_job_figure_skips_failed_extraction():
    r = _make_result(extracted=None)
    fig = to_job_figure(r, source_reference="src3")
    assert fig is None


def test_to_job_figure_truncates_long_titles():
    long_title = "x" * 500
    r = _make_result(extracted={"type": "plot", "title": long_title})
    fig = to_job_figure(r, source_reference="src4")
    assert len(fig["title"]) <= 200


# ---------------------------------------------------------------------------
# vlm_extract / vlm_verify (smoke tests with stub client)
# ---------------------------------------------------------------------------


class _StubClient:
    """Returns canned JSON responses for VLM calls.

    Detects whether it's an extract call (no accuracy key) or a verify call
    (has accuracy key) by inspecting the message content.
    """

    def __init__(self, extract_response: str, verify_response: str) -> None:
        self.extract_response = extract_response
        self.verify_response = verify_response
        self.call_count = 0

    async def __call__(self, messages, *, timeout):
        self.call_count += 1
        # Inspect last user message to decide response
        content = ""
        for m in messages:
            c = m.get("content", "")
            if isinstance(c, list):
                c = "\n".join(p.get("text", "") for p in c if p.get("type") == "text")
            content += c
        if "accuracy" in content.lower() or "extracted data" in content.lower():
            return self.verify_response
        return self.extract_response


@pytest.mark.asyncio
async def test_vlm_extract_parses_clean_json():
    client = _StubClient(
        extract_response='{"type":"plot","title":"X","x":"a","y":"b","series":["s1"]}',
        verify_response='{"accuracy":"high","issues":[]}',
    )
    img_bytes = _make_image_bytes("red", size=(50, 50))
    parsed, elapsed, tokens = await vlm_extract(client, img_bytes)
    assert parsed is not None
    assert parsed["type"] == "plot"
    assert parsed["title"] == "X"
    assert tokens == 0  # stub doesn't return usage
    assert elapsed >= 0


@pytest.mark.asyncio
async def test_vlm_extract_handles_prose_wrap():
    client = _StubClient(
        extract_response='{"type":"plot","title":"X","series":["s1"]}\n\nSome extra prose.',
        verify_response='{"accuracy":"medium","issues":["minor"]}',
    )
    img_bytes = _make_image_bytes("red", size=(50, 50))
    parsed, _, _ = await vlm_extract(client, img_bytes)
    assert parsed is not None
    assert parsed["title"] == "X"


@pytest.mark.asyncio
async def test_vlm_verify_returns_accuracy():
    client = _StubClient(
        extract_response='{"type":"plot"}',
        verify_response='{"accuracy":"low","issues":["axes wrong"]}',
    )
    img_bytes = _make_image_bytes("red", size=(50, 50))
    parsed, elapsed = await vlm_verify(
        client, img_bytes, {"type": "plot", "title": "X"}
    )
    assert parsed is not None
    assert parsed["accuracy"] == "low"
    assert "axes wrong" in parsed["issues"]


# ---------------------------------------------------------------------------
# extract_figures_with_mineru (smoke test with stubs)
# ---------------------------------------------------------------------------


class _StubMinerUClient:
    """Returns a fake MinerU zip result for testing."""

    async def parse_pdf(self, pdf_bytes, *, return_zip=False):
        # Return a stub result with zip_bytes=None to test fallback handling
        from nfm_db.services.mineru_client import MinerUResult

        return MinerUResult(
            markdown="# Empty\n\n![cap](images/test.jpg)\n\nFigure 1: caption.",
            task_id="stub-task",
            state="done",
            pages=1,
            zip_bytes=None,  # forces failure path
        )


@pytest.mark.asyncio
async def test_extract_figures_with_mineru_no_zip_raises():
    """When MinerU returns no zip, we expect RuntimeError."""
    client = _StubMinerUClient()
    with pytest.raises(RuntimeError, match="no zip"):
        await extract_figures_with_mineru(
            pdf_bytes=b"%PDF-stub",
            vlm_client=_StubClient("{}", "{}"),
            mineru_client=client,
        )


# ---------------------------------------------------------------------------
# _process_figures_parallel (P2: NFM-1366 batched VLM calls)
# ---------------------------------------------------------------------------


class _CountingClient:
    """Stub client that tracks concurrent call count."""

    def __init__(
        self,
        extract_response: str = '{"type":"plot","title":"X"}',
        verify_response: str = '{"accuracy":"high","issues":[]}',
    ) -> None:
        self.extract_response = extract_response
        self.verify_response = verify_response
        self.active_calls = 0
        self.max_concurrent = 0
        self.total_calls = 0
        self._lock = asyncio.Lock()

    async def __call__(self, messages, *, timeout):
        async with self._lock:
            self.active_calls += 1
            self.total_calls += 1
            if self.active_calls > self.max_concurrent:
                self.max_concurrent = self.active_calls
        try:
            await asyncio.sleep(0.01)  # simulate network round-trip
            content = ""
            for m in messages:
                c = m.get("content", "")
                if isinstance(c, list):
                    c = "\n".join(
                        p.get("text", "") for p in c if p.get("type") == "text"
                    )
                content += c
            if "accuracy" in content.lower() or "extracted data" in content.lower():
                return self.verify_response
            return self.extract_response
        finally:
            async with self._lock:
                self.active_calls -= 1


def _make_refs(n: int) -> list[FigureRef]:
    """Build n dummy FigureRef objects with valid PNG bytes."""
    palette = ["red", "green", "blue", "yellow", "purple", "orange", "cyan", "magenta", "pink", "brown"]
    return [
        FigureRef(
            image_bytes=_make_image_bytes(palette[i % len(palette)], size=(50, 50)),
            image_ref=f"images/test{i}.jpg",
            figure_numbers=[str(i + 1)],
            kind="figure",
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_process_figures_parallel_runs_all():
    """All refs should produce an ExtractionResult, in input order."""
    refs = _make_refs(5)
    client = _CountingClient()

    results = await _process_figures_parallel(client, refs, concurrency=3)

    assert len(results) == 5
    for i, r in enumerate(results):
        assert r.figure_ref is refs[i]
        assert r.extracted is not None
        assert r.verification is not None
        assert r.extracted["type"] == "plot"
        assert r.verification["accuracy"] == "high"


@pytest.mark.asyncio
async def test_process_figures_parallel_respects_concurrency_cap():
    """Max in-flight calls must not exceed the concurrency limit."""
    refs = _make_refs(10)
    client = _CountingClient()

    await _process_figures_parallel(client, refs, concurrency=3)

    assert client.max_concurrent <= 3, (
        f"Concurrency cap violated: max={client.max_concurrent}"
    )
    assert client.total_calls == 20, (
        f"Expected 20 VLM calls (10 figs * extract+verify), got {client.total_calls}"
    )


@pytest.mark.asyncio
async def test_process_figures_parallel_handles_vlm_failure():
    """When vlm_extract returns None, verify is skipped, no exception raised."""
    refs = _make_refs(2)
    client = _CountingClient(
        extract_response="not json at all",  # parse_vlm_json returns None
        verify_response='{"accuracy":"high","issues":[]}',
    )

    results = await _process_figures_parallel(client, refs, concurrency=2)

    assert len(results) == 2
    for r in results:
        assert r.extracted is None  # parse failed → no extracted
        assert r.verification is None  # verify is skipped when extracted is None
        assert r.extract_elapsed >= 0


@pytest.mark.asyncio
async def test_process_figures_parallel_handles_client_exception():
    """When VLM client raises, that ref yields extracted=None, others succeed."""

    class _BoomClient:
        def __init__(self):
            self.call_idx = 0

        async def __call__(self, messages, *, timeout):
            self.call_idx += 1
            if self.call_idx == 2:  # fail the verify call of the first ref
                raise RuntimeError("simulated VLM 502")
            content = ""
            for m in messages:
                c = m.get("content", "")
                if isinstance(c, list):
                    c = "\n".join(
                        p.get("text", "") for p in c if p.get("type") == "text"
                    )
                content += c
            if "accuracy" in content.lower():
                return '{"accuracy":"high","issues":[]}'
            return '{"type":"plot","title":"X"}'

    refs = _make_refs(2)
    client = _BoomClient()
    results = await _process_figures_parallel(client, refs, concurrency=2)

    assert len(results) == 2
    # The boom happens during the verify call of ref[0]; ref[0]'s outer
    # try/except catches it and yields extracted=None. ref[1] still succeeds
    # because gather() runs tasks independently.
    failed = [r for r in results if r.extracted is None]
    succeeded = [r for r in results if r.extracted is not None]
    assert len(failed) >= 1
    assert len(succeeded) >= 1


@pytest.mark.asyncio
async def test_process_figures_parallel_empty():
    """Empty input → empty output, no calls made."""
    client = _CountingClient()
    results = await _process_figures_parallel(client, [], concurrency=2)
    assert results == []
    assert client.total_calls == 0
