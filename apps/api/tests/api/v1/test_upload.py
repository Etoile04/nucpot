"""Tests for the chunked upload API (NFM-2024).

Covers all 6 acceptance criteria:
  AC-1: Upload init returns valid resume_token
  AC-2: Chunks can be uploaded in any order
  AC-3: Resume returns correct list of missing chunks after simulated interruption
  AC-4: Full-file SHA256 mismatch rejects the upload with clear error
  AC-5: Upload session status transitions: initiated -> uploading -> completed/failed
  AC-6: Unit test coverage >= 80%
"""

from __future__ import annotations

import hashlib
import io
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models.classification_level import ClassificationLevel
from nfm_db.models.hub_node import HubNode
from nfm_db.models.resource_node import ResourceNode
from nfm_db.services import chunk_upload_service as svc

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_HUB_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_NODE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_CL_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
async def db_with_nodes(db_session):
    """Seed hub_nodes, resource_nodes and classification_levels so FK constraints resolve."""
    hub = HubNode(
        id=_HUB_ID,
        name="test-hub",
        api_endpoint="https://hub.example.com",
    )
    db_session.add(hub)

    node = ResourceNode(
        id=_NODE_ID,
        hub_node_id=_HUB_ID,
        name="test-node",
        node_type="computing",
        api_endpoint="https://test.example.com",
        status="active",
    )
    db_session.add(node)

    cl = ClassificationLevel(
        id=_CL_ID,
        label="非密",
        description="Unclassified test level",
    )
    db_session.add(cl)
    await db_session.flush()
    yield db_session


@pytest.fixture
async def client(db_with_nodes):
    """Async test client with DB override and chunk storage override."""

    async def override_get_db():
        yield db_with_nodes

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def chunk_tmpdir(tmp_path):
    """Override chunk storage root to a temp directory."""
    svc.set_chunk_storage_root(tmp_path)
    yield tmp_path
    svc.set_chunk_storage_root(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _init_payload(
    file_name: str = "test.dat",
    total_size: int = 1024,
    sha256_full: str | None = None,
    chunk_size: int = 512,
) -> dict:
    if sha256_full is None:
        sha256_full = _sha256(b"\x00" * total_size)
    return {
        "resource_node_id": str(_NODE_ID),
        "classification_level_id": str(_CL_ID),
        "file_name": file_name,
        "total_size": total_size,
        "sha256_full": sha256_full,
        "chunk_size": chunk_size,
    }


# ---------------------------------------------------------------------------
# AC-1: Upload init returns valid resume_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_returns_resume_token(client, chunk_tmpdir):
    """AC-1: POST /upload/init returns session_id, resume_token, chunk_size, total_chunks."""
    payload = _init_payload(total_size=1500, chunk_size=512)
    resp = await client.post("/api/v1/upload/init", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert "session_id" in data
    assert "resume_token" in data
    assert data["resume_token"]
    assert data["chunk_size"] == 512
    # ceil(1500/512) = 3
    assert data["total_chunks"] == 3
    assert data["status"] == "initiated"


@pytest.mark.asyncio
async def test_init_missing_node_returns_404(client, chunk_tmpdir):
    """Init with a non-existent resource node returns 404."""
    payload = _init_payload()
    payload["resource_node_id"] = str(uuid.uuid4())
    resp = await client.post("/api/v1/upload/init", json=payload)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC-2: Chunks can be uploaded in any order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunks_uploaded_in_any_order(client, chunk_tmpdir):
    """AC-2: Upload chunks out of order (2, 0, 1) — all accepted."""
    full_data = b"A" * 512 + b"B" * 512 + b"C" * 512
    sha_full = _sha256(full_data)
    payload = _init_payload(total_size=1536, sha256_full=sha_full, chunk_size=512)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    chunks = [
        (2, full_data[1024:1536]),
        (0, full_data[0:512]),
        (1, full_data[512:1024]),
    ]
    for idx, data in chunks:
        files = {"chunk_data": ("chunk", io.BytesIO(data), "application/octet-stream")}
        form = {
            "resume_token": token,
            "chunk_index": str(idx),
            "sha256_chunk": _sha256(data),
        }
        resp = await client.post("/api/v1/upload/chunk", data=form, files=files)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["chunk_index"] == idx

    # All 3 uploaded
    assert resp.json()["data"]["uploaded_chunks"] == 3


# ---------------------------------------------------------------------------
# AC-3: Resume returns correct list of missing chunks after interruption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_returns_missing_chunks(client, chunk_tmpdir):
    """AC-3: After uploading chunk 0 only, resume reports [1, 2] missing."""
    full_data = b"A" * 512 + b"B" * 512 + b"C" * 512
    sha_full = _sha256(full_data)
    payload = _init_payload(total_size=1536, sha256_full=sha_full, chunk_size=512)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    # Upload only chunk 0
    files = {"chunk_data": ("chunk", io.BytesIO(full_data[0:512]), "application/octet-stream")}
    form = {"resume_token": token, "chunk_index": "0", "sha256_chunk": _sha256(full_data[0:512])}
    await client.post("/api/v1/upload/chunk", data=form, files=files)

    # Resume
    resp = await client.post("/api/v1/upload/resume", json={"resume_token": token})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["missing_chunks"] == [1, 2]
    assert data["uploaded_chunks"] == 1
    assert data["total_chunks"] == 3


@pytest.mark.asyncio
async def test_resume_all_chunks_present(client, chunk_tmpdir):
    """Resume after all chunks uploaded reports empty missing list."""
    full_data = b"A" * 512 + b"B" * 512
    sha_full = _sha256(full_data)
    payload = _init_payload(total_size=1024, sha256_full=sha_full, chunk_size=512)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    for idx in range(2):
        chunk = full_data[idx * 512 : (idx + 1) * 512]
        files = {"chunk_data": ("chunk", io.BytesIO(chunk), "application/octet-stream")}
        form = {"resume_token": token, "chunk_index": str(idx), "sha256_chunk": _sha256(chunk)}
        await client.post("/api/v1/upload/chunk", data=form, files=files)

    resp = await client.post("/api/v1/upload/resume", json={"resume_token": token})
    data = resp.json()["data"]
    assert data["missing_chunks"] == []


@pytest.mark.asyncio
async def test_resume_bad_token_returns_404(client, chunk_tmpdir):
    """Resume with an invalid token returns 404."""
    resp = await client.post("/api/v1/upload/resume", json={"resume_token": "invalid"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC-4: Full-file SHA256 mismatch rejects the upload with clear error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sha256_mismatch_rejects_upload(client, chunk_tmpdir):
    """AC-4: Complete with wrong SHA256 marks session as failed."""
    full_data = b"A" * 512 + b"B" * 512
    wrong_sha = "0" * 64  # intentionally wrong
    payload = _init_payload(total_size=1024, sha256_full=wrong_sha, chunk_size=512)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    # Upload both chunks
    for idx in range(2):
        chunk = full_data[idx * 512 : (idx + 1) * 512]
        files = {"chunk_data": ("chunk", io.BytesIO(chunk), "application/octet-stream")}
        form = {"resume_token": token, "chunk_index": str(idx), "sha256_chunk": _sha256(chunk)}
        await client.post("/api/v1/upload/chunk", data=form, files=files)

    # Complete — should fail due to SHA256 mismatch
    resp = await client.post("/api/v1/upload/complete", json={"resume_token": token})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "failed"
    assert "SHA256 mismatch" in (data.get("error") or "")


# ---------------------------------------------------------------------------
# AC-5: Upload session status transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_initiated_to_in_progress(client, chunk_tmpdir):
    """AC-5: Uploading first chunk transitions status from initiated to in_progress."""
    full_data = b"X" * 1024
    sha_full = _sha256(full_data)
    payload = _init_payload(total_size=1024, sha256_full=sha_full, chunk_size=1024)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    assert init_resp.json()["data"]["status"] == "initiated"
    token = init_resp.json()["data"]["resume_token"]

    # Upload chunk 0
    chunk = full_data[0:1024]
    files = {"chunk_data": ("chunk", io.BytesIO(chunk), "application/octet-stream")}
    form = {"resume_token": token, "chunk_index": "0", "sha256_chunk": _sha256(chunk)}
    chunk_resp = await client.post("/api/v1/upload/chunk", data=form, files=files)
    assert chunk_resp.json()["data"]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_status_in_progress_to_completed(client, chunk_tmpdir):
    """AC-5: Successful complete transitions to completed."""
    full_data = b"Y" * 512 + b"Z" * 512
    sha_full = _sha256(full_data)
    payload = _init_payload(total_size=1024, sha256_full=sha_full, chunk_size=512)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    for idx in range(2):
        chunk = full_data[idx * 512 : (idx + 1) * 512]
        files = {"chunk_data": ("chunk", io.BytesIO(chunk), "application/octet-stream")}
        form = {"resume_token": token, "chunk_index": str(idx), "sha256_chunk": _sha256(chunk)}
        await client.post("/api/v1/upload/chunk", data=form, files=files)

    resp = await client.post("/api/v1/upload/complete", json={"resume_token": token})
    assert resp.json()["data"]["status"] == "completed"


@pytest.mark.asyncio
async def test_status_failed_no_further_chunks(client, chunk_tmpdir):
    """AC-5: After failed status, further chunk uploads are rejected."""
    full_data = b"W" * 512 + b"X" * 512
    wrong_sha = "f" * 64
    payload = _init_payload(total_size=1024, sha256_full=wrong_sha, chunk_size=512)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    # Upload both chunks
    for idx in range(2):
        chunk = full_data[idx * 512 : (idx + 1) * 512]
        files = {"chunk_data": ("chunk", io.BytesIO(chunk), "application/octet-stream")}
        form = {"resume_token": token, "chunk_index": str(idx), "sha256_chunk": _sha256(chunk)}
        await client.post("/api/v1/upload/chunk", data=form, files=files)

    # Complete — fails
    await client.post("/api/v1/upload/complete", json={"resume_token": token})

    # Try another chunk — should be rejected
    extra_chunk = b"Q" * 512
    files = {"chunk_data": ("chunk", io.BytesIO(extra_chunk), "application/octet-stream")}
    form = {"resume_token": token, "chunk_index": "0", "sha256_chunk": _sha256(extra_chunk)}
    resp = await client.post("/api/v1/upload/chunk", data=form, files=files)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Per-chunk SHA256 verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_sha256_mismatch_rejected(client, chunk_tmpdir):
    """Uploading a chunk with wrong SHA256 is rejected."""
    full_data = b"Z" * 1024
    sha_full = _sha256(full_data)
    payload = _init_payload(total_size=1024, sha256_full=sha_full, chunk_size=1024)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    # Upload chunk with wrong SHA256
    files = {"chunk_data": ("chunk", io.BytesIO(b"Z" * 1024), "application/octet-stream")}
    form = {"resume_token": token, "chunk_index": "0", "sha256_chunk": "a" * 64}
    resp = await client.post("/api/v1/upload/chunk", data=form, files=files)
    assert resp.status_code == 400
    assert "SHA256 mismatch" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_with_missing_chunks(client, chunk_tmpdir):
    """Complete without all chunks returns in_progress with error."""
    full_data = b"M" * 1024
    sha_full = _sha256(full_data)
    payload = _init_payload(total_size=1024, sha256_full=sha_full, chunk_size=512)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    # Upload only chunk 0
    chunk = full_data[0:512]
    files = {"chunk_data": ("chunk", io.BytesIO(chunk), "application/octet-stream")}
    form = {"resume_token": token, "chunk_index": "0", "sha256_chunk": _sha256(chunk)}
    await client.post("/api/v1/upload/chunk", data=form, files=files)

    # Complete — missing chunks
    resp = await client.post("/api/v1/upload/complete", json={"resume_token": token})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "in_progress"
    assert "Missing chunks" in (data.get("error") or "")


@pytest.mark.asyncio
async def test_chunk_index_out_of_range(client, chunk_tmpdir):
    """Chunk index exceeding total_chunks returns 400."""
    full_data = b"O" * 512
    sha_full = _sha256(full_data)
    payload = _init_payload(total_size=512, sha256_full=sha_full, chunk_size=512)
    init_resp = await client.post("/api/v1/upload/init", json=payload)
    token = init_resp.json()["data"]["resume_token"]

    files = {"chunk_data": ("chunk", io.BytesIO(b"O" * 512), "application/octet-stream")}
    form = {"resume_token": token, "chunk_index": "5", "sha256_chunk": _sha256(b"O" * 512)}
    resp = await client.post("/api/v1/upload/chunk", data=form, files=files)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Service-level unit tests (direct service calls, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_generate_resume_token_uniqueness():
    """Resume tokens should be unique across calls."""
    tokens = {svc._generate_resume_token() for _ in range(100)}
    assert len(tokens) == 100


@pytest.mark.asyncio
async def test_service_list_missing_chunks(chunk_tmpdir):
    """Missing chunk detection works correctly."""
    session_dir = chunk_tmpdir / str(uuid.uuid4())
    session_dir.mkdir(parents=True)
    (session_dir / "0").write_bytes(b"a")
    (session_dir / "2").write_bytes(b"c")
    # Chunks 0 and 2 exist, 1 is missing
    missing = svc._list_missing_chunks(session_dir, 3)
    assert missing == [1]


@pytest.mark.asyncio
async def test_service_assemble_and_hash(chunk_tmpdir):
    """Assembly reads chunks in order and returns concatenated bytes."""
    session_dir = chunk_tmpdir / str(uuid.uuid4())
    session_dir.mkdir(parents=True)
    (session_dir / "0").write_bytes(b"AB")
    (session_dir / "1").write_bytes(b"CD")
    result = svc._assemble_and_hash(session_dir, 2)
    assert result == b"ABCD"


@pytest.mark.asyncio
async def test_service_compute_sha256():
    """SHA256 computation is correct."""
    assert svc._compute_sha256(b"hello") == hashlib.sha256(b"hello").hexdigest()


# ---------------------------------------------------------------------------
# Additional service-level tests for coverage gaps (NFM-2527)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_resolve_chunk_root_env_fallback():
    """_resolve_chunk_root falls back to env var when override is None."""
    import os

    svc.set_chunk_storage_root(None)
    os.environ["CHUNK_STORAGE_ROOT"] = "/tmp/test_chunks"
    try:
        result = svc._resolve_chunk_root()
        assert result == svc.Path("/tmp/test_chunks")
    finally:
        os.environ.pop("CHUNK_STORAGE_ROOT", None)


@pytest.mark.asyncio
async def test_service_assemble_and_hash_missing_chunk_raises(chunk_tmpdir):
    """_assemble_and_hash raises ValueError when a chunk is missing."""
    session_dir = chunk_tmpdir / str(uuid.uuid4())
    session_dir.mkdir(parents=True)
    (session_dir / "0").write_bytes(b"AB")
    # Chunk 1 is missing — should raise
    with pytest.raises(ValueError, match="Chunk 1 is missing"):
        svc._assemble_and_hash(session_dir, 2)


@pytest.mark.asyncio
async def test_service_init_upload_missing_classification_level(db_with_nodes, chunk_tmpdir):
    """init_upload returns 404 when classification level does not exist."""
    from nfm_db.schemas.data_submission import UploadInitRequest

    payload = UploadInitRequest(
        resource_node_id=_NODE_ID,
        classification_level_id=uuid.uuid4(),  # non-existent
        file_name="test.dat",
        total_size=1024,
        sha256_full=hashlib.sha256(b"\x00" * 1024).hexdigest(),
        chunk_size=512,
    )
    with pytest.raises(Exception) as exc_info:
        await svc.init_upload(db_with_nodes, payload)
    assert exc_info.value.status_code == 404
    assert "Classification level" in exc_info.value.detail


@pytest.mark.asyncio
async def test_service_complete_upload_already_completed(db_with_nodes, chunk_tmpdir):
    """complete_upload returns early when session is already completed."""
    from nfm_db.models.upload_session import UploadSession
    from nfm_db.schemas.data_submission import UploadInitRequest

    # Create a completed session directly
    init_payload = UploadInitRequest(
        resource_node_id=_NODE_ID,
        classification_level_id=_CL_ID,
        file_name="done.dat",
        total_size=512,
        sha256_full=hashlib.sha256(b"\x00" * 512).hexdigest(),
        chunk_size=512,
    )
    init_result = await svc.init_upload(db_with_nodes, init_payload)
    token = init_result.resume_token

    # Mark as completed
    session = await db_with_nodes.get(UploadSession, init_result.session_id)
    session.status = "completed"
    await db_with_nodes.commit()
    await db_with_nodes.refresh(session)

    # complete_upload should return completed status, not re-process
    result = await svc.complete_upload(db_with_nodes, token, chunk_root=chunk_tmpdir)
    assert result.status == "completed"
    assert "already" in (result.error or "")


@pytest.mark.asyncio
async def test_service_resume_upload_completed_status(db_with_nodes, chunk_tmpdir):
    """resume_upload returns terminal state without checking disk for completed sessions."""
    from nfm_db.models.upload_session import UploadSession
    from nfm_db.schemas.data_submission import UploadInitRequest

    # Create session and mark completed
    init_payload = UploadInitRequest(
        resource_node_id=_NODE_ID,
        classification_level_id=_CL_ID,
        file_name="done2.dat",
        total_size=512,
        sha256_full=hashlib.sha256(b"\x00" * 512).hexdigest(),
        chunk_size=512,
    )
    init_result = await svc.init_upload(db_with_nodes, init_payload)
    token = init_result.resume_token

    session = await db_with_nodes.get(UploadSession, init_result.session_id)
    session.status = "completed"
    session.uploaded_chunks = session.total_chunks
    await db_with_nodes.commit()

    # Resume should return completed with empty missing_chunks
    result = await svc.resume_upload(db_with_nodes, token, chunk_root=chunk_tmpdir)
    assert result.status == "completed"
    assert result.missing_chunks == []
    assert result.uploaded_chunks == session.total_chunks


@pytest.mark.asyncio
async def test_service_resume_upload_failed_status(db_with_nodes, chunk_tmpdir):
    """resume_upload returns terminal state for failed sessions."""
    from nfm_db.models.upload_session import UploadSession
    from nfm_db.schemas.data_submission import UploadInitRequest

    init_payload = UploadInitRequest(
        resource_node_id=_NODE_ID,
        classification_level_id=_CL_ID,
        file_name="fail.dat",
        total_size=512,
        sha256_full=hashlib.sha256(b"\x00" * 512).hexdigest(),
        chunk_size=512,
    )
    init_result = await svc.init_upload(db_with_nodes, init_payload)
    token = init_result.resume_token

    session = await db_with_nodes.get(UploadSession, init_result.session_id)
    session.status = "failed"
    await db_with_nodes.commit()

    result = await svc.resume_upload(db_with_nodes, token, chunk_root=chunk_tmpdir)
    assert result.status == "failed"
    assert result.missing_chunks == []


@pytest.mark.asyncio
async def test_service_count_existing_chunks(chunk_tmpdir):
    """_count_existing_chunks returns correct count."""
    session_dir = chunk_tmpdir / str(uuid.uuid4())
    session_dir.mkdir(parents=True)
    (session_dir / "0").write_bytes(b"a")
    (session_dir / "2").write_bytes(b"c")
    assert svc._count_existing_chunks(session_dir, 3) == 2
    assert svc._count_existing_chunks(session_dir, 4) == 2


@pytest.mark.asyncio
async def test_service_chunk_dir_for(chunk_tmpdir):
    """_chunk_dir_for creates directory and returns path."""
    import nfm_db.services.chunk_upload_service as mod

    sid = uuid.uuid4()
    result = mod._chunk_dir_for(chunk_tmpdir, sid)
    assert result.exists()
    assert result == chunk_tmpdir / str(sid)
