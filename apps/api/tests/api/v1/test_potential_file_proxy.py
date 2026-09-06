"""Integration tests for the canonical potential-file download proxy (NFM-4309).

``GET /api/v1/potentials/{id}/file`` is the single canonical download URL
for every potential that has a file (BUG-37):

* anonymous — no auth headers required;
* resolves every historical storage form (uploads volume, container-path
  legacy rows, Supabase objects, multi-object rows via ``?index=``);
* new uploads persist the canonical URL plus an ``extra.file_storage``
  reference instead of a site-relative or container path.
"""

from __future__ import annotations

import asyncio
import gc
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select

from nfm_db.main import app
from nfm_db.models.potential import Potential
from nfm_db.services.potential_file_resolver import canonical_file_url
from nfm_db.services.upload_service import get_upload_dir


async def _seed(
    db_session,
    *,
    name: str,
    file_url: str | None,
    extra: dict | None = None,
) -> Potential:
    potential = Potential(
        name=name,
        display_name=name,
        type="EAM",
        elements=["U"],
        description=f"{name} description",
        system_name=f"System_{name}",
        status="published",
        file_url=file_url,
        extra=extra if extra is not None else {},
    )
    db_session.add(potential)
    await db_session.commit()
    await db_session.refresh(potential)
    return potential


@pytest.fixture
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the API upload directory at a temp dir for this test."""
    d = tmp_path / "uploads"
    d.mkdir()
    monkeypatch.setattr("nfm_db.services.upload_service._UPLOAD_DIR_OVERRIDE", d)
    app.dependency_overrides[get_upload_dir] = lambda: d
    yield d
    app.dependency_overrides.pop(get_upload_dir, None)


# ---------------------------------------------------------------------------
# Anonymous downloads — uploads volume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_uploads_storage_ref_anonymous(async_client, db_session, upload_dir) -> None:
    (upload_dir / "abc.tersoff").write_bytes(b"# tersoff body\n")
    pot = await _seed(
        db_session,
        name="proxy-uploads-ref",
        file_url=canonical_file_url("00000000-0000-0000-0000-000000000001"),
        extra={"file_storage": {"kind": "uploads", "key": "abc.tersoff"}},
    )

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 200
    assert response.content == b"# tersoff body\n"
    assert "tersoff" in response.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_download_legacy_uploads_relative_row(async_client, db_session, upload_dir) -> None:
    """Unmigrated legacy rows (/uploads/<key>) resolve through the volume too."""
    (upload_dir / "14607d0a.tersoff").write_bytes(b"legacy bytes")
    pot = await _seed(db_session, name="legacy-uploads", file_url="/uploads/14607d0a.tersoff")

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 200
    assert response.content == b"legacy bytes"


@pytest.mark.asyncio
async def test_download_legacy_container_path_row(async_client, db_session, upload_dir) -> None:
    """BUG-37 form-2 rows (/app/uploads/<file>) map onto the shared volume."""
    (upload_dir / "Fe_Mendelev_2007v2.eam.fs").write_bytes(b"eam fs body")
    pot = await _seed(
        db_session,
        name="legacy-container",
        file_url="/app/uploads/Fe_Mendelev_2007v2.eam.fs",
    )

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 200
    assert response.content == b"eam fs body"


@pytest.mark.asyncio
async def test_download_missing_file_returns_404(async_client, db_session, upload_dir) -> None:
    pot = await _seed(
        db_session,
        name="gone-file",
        file_url=canonical_file_url("00000000-0000-0000-0000-000000000002"),
        extra={"file_storage": {"kind": "uploads", "key": "missing.tersoff"}},
    )

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Anonymous downloads — Supabase objects (streamed via httpx)
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    # httpx Response surface used by the proxy
    status_code = 200
    headers = {"content-type": "application/octet-stream", "content-length": "9"}

    async def aiter_bytes(self, chunk_size: int):
        yield self._payload


class _FakeClient:
    """Records requested URLs; serves a fixed payload."""

    requested: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs):
        type(self).requested.append(url)
        return _FakeStream(b"mtp bytes")


@pytest.mark.asyncio
async def test_download_supabase_object_streams_through_backend(
    async_client, db_session, monkeypatch
) -> None:
    pot = await _seed(
        db_session,
        name="supabase-single",
        file_url=canonical_file_url("00000000-0000-0000-0000-000000000003"),
        extra={
            "file_storage": {
                "kind": "supabase",
                "objects": ["potentials/huda/Ag2S_MTP.mtp"],
            }
        },
    )
    _FakeClient.requested = []
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 200
    assert response.content == b"mtp bytes"
    assert _FakeClient.requested == [
        "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/huda/Ag2S_MTP.mtp"
    ]


@pytest.mark.asyncio
async def test_download_supabase_multi_object_index(async_client, db_session, monkeypatch) -> None:
    """Multi-file rows fetch object ?index= (default 0)."""
    pot = await _seed(
        db_session,
        name="supabase-multi",
        file_url=canonical_file_url("00000000-0000-0000-0000-000000000004"),
        extra={
            "file_storage": {
                "kind": "supabase",
                "objects": [
                    "potentials/library/FeCr_Bonny_2011_d.eam.alloy",
                    "potentials/library/FeCr_Bonny_2011_s.eam.fs",
                ],
            }
        },
    )
    _FakeClient.requested = []
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    first = await async_client.get(f"/api/v1/potentials/{pot.id}/file")
    second = await async_client.get(f"/api/v1/potentials/{pot.id}/file?index=1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert _FakeClient.requested[0].endswith("FeCr_Bonny_2011_d.eam.alloy")
    assert _FakeClient.requested[1].endswith("FeCr_Bonny_2011_s.eam.fs")


@pytest.mark.asyncio
async def test_download_supabase_index_out_of_range(async_client, db_session, monkeypatch) -> None:
    pot = await _seed(
        db_session,
        name="supabase-oob",
        file_url=canonical_file_url("00000000-0000-0000-0000-000000000005"),
        extra={"file_storage": {"kind": "supabase", "objects": ["potentials/x.mtp"]}},
    )
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file?index=7")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_legacy_supabase_relative_row(async_client, db_session, monkeypatch) -> None:
    """Unmigrated /storage/v1/... rows resolve via the Supabase public URL."""
    pot = await _seed(
        db_session,
        name="legacy-supabase",
        file_url="/storage/v1/object/public/potentials/huda/Ag2S_MTP.mtp",
    )
    _FakeClient.requested = []
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 200
    assert response.content == b"mtp bytes"


@pytest.mark.asyncio
async def test_download_foreign_origin_redirects_without_server_fetch(
    async_client, db_session, monkeypatch
) -> None:
    """Foreign-origin object URLs are handed to the browser (SSRF guard).

    Pre-change, absolute URLs were fetched by the browser; the proxy must
    not turn them into unauthenticated server-side fetches of arbitrary
    (possibly internal) addresses.
    """
    foreign = "https://example.com/some/pot.dat"
    pot = await _seed(
        db_session,
        name="foreign-origin",
        file_url=canonical_file_url("00000000-0000-0000-0000-000000000006"),
        extra={"file_storage": {"kind": "supabase", "objects": [foreign]}},
    )
    _FakeClient.requested = []
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 307
    assert response.headers["location"] == foreign
    assert _FakeClient.requested == []


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_unknown_potential_404(async_client) -> None:
    response = await async_client.get(
        "/api/v1/potentials/00000000-0000-0000-0000-00000000000f/file"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_row_without_file_404(async_client, db_session) -> None:
    pot = await _seed(db_session, name="no-file", file_url=None)

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_canonical_url_without_storage_ref_404(async_client, db_session) -> None:
    """Canonical file_url alone carries no location — unresolvable → 404."""
    pot = await _seed(
        db_session,
        name="dangling-canonical",
        file_url="/api/v1/potentials/00000000-0000-0000-0000-000000000009/file",
    )

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Upload write path persists the canonical form (BUG-37 spec)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_persists_canonical_url_and_storage_ref(
    async_client, db_session, upload_dir
) -> None:
    pot = await _seed(db_session, name="upload-canonical", file_url=None)

    response = await async_client.post(
        f"/api/v1/potentials/{pot.id}/file",
        files={"file": ("U_Mo.eam.alloy", b"fake eam body", "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]

    assert body["file_url"] == f"/api/v1/potentials/{pot.id}/file"
    assert body["file_path"] == f"/api/v1/potentials/{pot.id}/file"

    # DB row: canonical URL + storage reference, no container path anywhere.
    row = (await db_session.execute(select(Potential).where(Potential.id == pot.id))).scalar_one()
    assert row.file_url == f"/api/v1/potentials/{pot.id}/file"
    assert row.extra["file_storage"]["kind"] == "uploads"
    assert row.extra["file_storage"]["key"].endswith(".eam.alloy")

    # Round trip: the canonical URL downloads the uploaded bytes anonymously.
    download = await async_client.get(f"/api/v1/potentials/{pot.id}/file")
    assert download.status_code == 200
    assert download.content == b"fake eam body"


# ---------------------------------------------------------------------------
# Real-HTTP regression: streamed bodies must survive awaits (gate round 2)
# ---------------------------------------------------------------------------

_OBJECT_SERVER_PAYLOAD = b"# regression payload - remote file stream body\n" * 8


class _ObjectServerHandler(BaseHTTPRequestHandler):
    """Serves a fixed payload for every GET (stand-in public object store)."""

    def do_GET(self) -> None:
        body = _OBJECT_SERVER_PAYLOAD
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence test output
        return


@pytest.fixture
def object_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Real localhost HTTP server bound to the Supabase public origin env."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ObjectServerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("NFM_SUPABASE_PUBLIC_ORIGIN", origin)
    yield origin
    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_remote_file_stream_keeps_upstream_body_alive(object_server: str) -> None:
    """Regression (NFM-4309 gate test round): ``RemoteFileStream`` must retain
    the httpx stream context manager for the stream's lifetime.

    Dropping the only reference lets CPython finalize the ``client.stream``
    async generator, whose unwind closes the upstream response — after any
    intervening await, body iteration then fails with ``httpx.StreamClosed``
    and the proxy returns a truncated (empty) 200 body.  The fake-client
    unit tests cannot catch this because they substitute a plain object for
    the real context manager.
    """
    from nfm_db.api.v1.potentials import RemoteFileStream

    url = f"{object_server}/storage/v1/object/public/potentials/regression.mtp"
    stream = await RemoteFileStream.open(url)
    try:
        await asyncio.sleep(0.02)  # intervening await → finalization window
        gc.collect()  # force async-generator finalization deterministically
        chunks = [chunk async for chunk in stream.aiter_bytes()]
    finally:
        await stream.aclose()

    assert b"".join(chunks) == _OBJECT_SERVER_PAYLOAD


@pytest.mark.asyncio
async def test_download_supabase_object_body_over_real_http(
    async_client, db_session, object_server
) -> None:
    """The endpoint streams the full upstream body over real httpx.

    Unit tests fake ``httpx.AsyncClient`` entirely; this exercises the real
    client against a real (localhost) object server so an empty-body 200 —
    the exact production symptom of BUG-37's proxy — fails loudly.
    """
    pot = await _seed(
        db_session,
        name="supabase-real-http",
        file_url=canonical_file_url("00000000-0000-0000-0000-0000000000a1"),
        extra={
            "file_storage": {"kind": "supabase", "objects": ["potentials/regression.mtp"]}
        },
    )

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 200
    assert response.content == _OBJECT_SERVER_PAYLOAD


# ---------------------------------------------------------------------------
# download_count (grilling decision #1154 item 3 — proxy hit statistics)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_increments_download_count(async_client, db_session, upload_dir) -> None:
    """Every successful proxy hit atomically bumps extra.download_count."""
    (upload_dir / "counted.eam").write_bytes(b"counted body")
    pot = await _seed(
        db_session,
        name="dl-count",
        file_url=canonical_file_url("00000000-0000-0000-0000-0000000000b1"),
        extra={"file_storage": {"kind": "uploads", "key": "counted.eam"}},
    )

    first = await async_client.get(f"/api/v1/potentials/{pot.id}/file")
    second = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert first.status_code == 200
    assert second.status_code == 200

    row = (await db_session.execute(select(Potential).where(Potential.id == pot.id))).scalar_one()
    # Async refresh — identity-map attribute access on an expired row would
    # be sync IO (MissingGreenlet), and without expiry the cached ``extra``
    # from seeding hides the endpoint's committed UPDATE.
    await db_session.refresh(row)
    assert row.extra["download_count"] == 2


@pytest.mark.asyncio
async def test_download_404_does_not_increment_download_count(
    async_client, db_session, upload_dir
) -> None:
    """Failed resolutions are not downloads — the counter must stay absent."""
    pot = await _seed(
        db_session,
        name="dl-count-miss",
        file_url=canonical_file_url("00000000-0000-0000-0000-0000000000b2"),
        extra={"file_storage": {"kind": "uploads", "key": "vanished.eam"}},
    )

    response = await async_client.get(f"/api/v1/potentials/{pot.id}/file")

    assert response.status_code == 404
    row = (await db_session.execute(select(Potential).where(Potential.id == pot.id))).scalar_one()
    await db_session.refresh(row)
    assert "download_count" not in row.extra


@pytest.mark.asyncio
async def test_download_count_exposed_on_detail(async_client, db_session, upload_dir) -> None:
    """PotentialDetail surfaces extra.download_count as a top-level field."""
    (upload_dir / "exposed.eam").write_bytes(b"exposed body")
    pot = await _seed(
        db_session,
        name="dl-expose",
        file_url=canonical_file_url("00000000-0000-0000-0000-0000000000b3"),
        extra={"file_storage": {"kind": "uploads", "key": "exposed.eam"}},
    )

    before = await async_client.get(f"/api/v1/potentials/{pot.id}")
    assert before.json()["data"]["download_count"] == 0

    assert (await async_client.get(f"/api/v1/potentials/{pot.id}/file")).status_code == 200

    # The app shares this session; expire so the detail SELECT reloads
    # ``extra`` instead of trusting the identity map's pre-download copy.
    # (Read pot.id first — attribute access on an expired row is sync IO.)
    pid = pot.id
    db_session.expire_all()
    after = await async_client.get(f"/api/v1/potentials/{pid}")
    assert after.json()["data"]["download_count"] == 1
