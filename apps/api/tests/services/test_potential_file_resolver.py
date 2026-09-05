"""Unit tests for the canonical file_url governance module (NFM-4309 / BUG-37).

Covers the classification of the three (plus one absolute) historical
``potentials.file_url`` forms, the canonical proxy-URL contract, the
storage-ref mapping used by the download proxy, and the ingestion shape
validator that must refuse container-path URLs.
"""

from __future__ import annotations

import uuid

import pytest

from nfm_db.services.potential_file_resolver import (
    FileUrlForm,
    canonical_file_url,
    classify_file_url,
    download_count_bind_value,
    download_count_update_sql,
    is_supabase_public_url,
    public_object_url,
    split_file_url_objects,
    storage_ref_from_file_url,
    supabase_public_origin,
    validate_persistable_file_url,
)
from nfm_db.services.upload_service import PotentialUploadError

# ---------------------------------------------------------------------------
# classify_file_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (None, FileUrlForm.EMPTY),
        ("", FileUrlForm.EMPTY),
        ("   ", FileUrlForm.EMPTY),
        ("/app/uploads/Fe_Mendelev_2007v2.eam.fs", FileUrlForm.FILESYSTEM_PATH),
        ("/etc/passwd", FileUrlForm.FILESYSTEM_PATH),
        ("/var/log/nfmd/x", FileUrlForm.FILESYSTEM_PATH),
        ("file:///app/uploads/x.eam.fs", FileUrlForm.FILESYSTEM_PATH),
        ("C:\\app\\uploads\\x.eam.fs", FileUrlForm.FILESYSTEM_PATH),
        ("/uploads/14607d0a-1a7b-49fd-9b22-1cd5671864c8.tersoff", FileUrlForm.UPLOADS_RELATIVE),
        ("/uploads/<uuid>/nested.eam.alloy", FileUrlForm.UPLOADS_RELATIVE),
        (
            "/storage/v1/object/public/potentials/huda/Ag2S_MTP.mtp",
            FileUrlForm.SUPABASE_RELATIVE,
        ),
        (
            "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/library/Al_Mendelev_2008.eam.fs",
            FileUrlForm.HTTP_ABSOLUTE,
        ),
        ("https://example.com/some/pot.dat", FileUrlForm.HTTP_ABSOLUTE),
        (
            "/api/v1/potentials/14607d0a-1a7b-49fd-9b22-1cd5671864c8/file",
            FileUrlForm.CANONICAL_PROXY,
        ),
        ("bare.eam.alloy", FileUrlForm.BARE_FILENAME),
    ],
)
def test_classify_file_url(url, expected) -> None:
    assert classify_file_url(url) is expected


# ---------------------------------------------------------------------------
# canonical_file_url
# ---------------------------------------------------------------------------


def test_canonical_file_url_shape() -> None:
    pid = uuid.uuid4()
    assert canonical_file_url(pid) == f"/api/v1/potentials/{pid}/file"


def test_classify_roundtrip_canonical() -> None:
    url = canonical_file_url(uuid.uuid4())
    assert classify_file_url(url) is FileUrlForm.CANONICAL_PROXY


# ---------------------------------------------------------------------------
# split_file_url_objects — comma-separated multi-object fields
# ---------------------------------------------------------------------------


def test_split_single_object() -> None:
    assert split_file_url_objects("/storage/v1/object/public/potentials/a.mtp") == [
        "/storage/v1/object/public/potentials/a.mtp"
    ]


def test_split_multi_object_strips_whitespace() -> None:
    url = (
        "/storage/v1/object/public/potentials/library/d.eam.alloy, "
        "/storage/v1/object/public/potentials/library/s.eam.fs"
    )
    assert split_file_url_objects(url) == [
        "/storage/v1/object/public/potentials/library/d.eam.alloy",
        "/storage/v1/object/public/potentials/library/s.eam.fs",
    ]


# ---------------------------------------------------------------------------
# storage_ref_from_file_url
# ---------------------------------------------------------------------------


def test_storage_ref_uploads_relative_flat() -> None:
    ref = storage_ref_from_file_url("/uploads/abc.tersoff")
    assert ref == {"kind": "uploads", "key": "abc.tersoff"}


def test_storage_ref_uploads_relative_nested() -> None:
    ref = storage_ref_from_file_url("/uploads/<uuid>/file.eam.alloy")
    assert ref == {"kind": "uploads", "key": "<uuid>/file.eam.alloy"}


def test_storage_ref_container_path_maps_to_uploads_volume() -> None:
    """The BUG-37 form-2 container path must map onto the shared uploads volume."""
    ref = storage_ref_from_file_url("/app/uploads/Fe_Mendelev_2007v2.eam.fs")
    assert ref == {"kind": "uploads", "key": "Fe_Mendelev_2007v2.eam.fs"}


def test_storage_ref_supabase_relative() -> None:
    ref = storage_ref_from_file_url("/storage/v1/object/public/potentials/huda/Ag2S_MTP.mtp")
    assert ref == {"kind": "supabase", "objects": ["potentials/huda/Ag2S_MTP.mtp"]}


def test_storage_ref_supabase_multi_object() -> None:
    url = (
        "/storage/v1/object/public/potentials/library/FeCr_Bonny_2011_d.eam.alloy,"
        "/storage/v1/object/public/potentials/library/FeCr_Bonny_2011_s.eam.fs"
    )
    ref = storage_ref_from_file_url(url)
    assert ref == {
        "kind": "supabase",
        "objects": [
            "potentials/library/FeCr_Bonny_2011_d.eam.alloy",
            "potentials/library/FeCr_Bonny_2011_s.eam.fs",
        ],
    }


def test_storage_ref_http_absolute_strips_supabase_origin() -> None:
    ref = storage_ref_from_file_url(
        "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/library/Al_Mendelev_2008.eam.fs"
    )
    assert ref == {"kind": "supabase", "objects": ["potentials/library/Al_Mendelev_2008.eam.fs"]}


def test_storage_ref_http_absolute_foreign_origin_kept_verbatim() -> None:
    ref = storage_ref_from_file_url("https://example.com/some/pot.dat")
    assert ref == {"kind": "supabase", "objects": ["https://example.com/some/pot.dat"]}


def test_storage_ref_canonical_proxy_unresolvable() -> None:
    """A canonical proxy URL carries no storage location by itself."""
    assert storage_ref_from_file_url(canonical_file_url(uuid.uuid4())) is None


def test_storage_ref_empty_and_bare_unresolvable() -> None:
    assert storage_ref_from_file_url(None) is None
    assert storage_ref_from_file_url("bare.eam.alloy") is None


# ---------------------------------------------------------------------------
# public_object_url
# ---------------------------------------------------------------------------


def test_public_object_url_relative() -> None:
    url = public_object_url("potentials/huda/Ag2S_MTP.mtp")
    assert url == (
        "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/huda/Ag2S_MTP.mtp"
    )


def test_public_object_url_absolute_passthrough() -> None:
    absolute = "https://example.com/some/pot.dat"
    assert public_object_url(absolute) == absolute


def test_public_object_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NFM_SUPABASE_PUBLIC_ORIGIN", "https://stub.supabase.co")
    assert (
        public_object_url("bucket/x.mtp")
        == "https://stub.supabase.co/storage/v1/object/public/bucket/x.mtp"
    )


# ---------------------------------------------------------------------------
# is_supabase_public_url — server-side fetch origin guard (SSRF)
# ---------------------------------------------------------------------------


def test_is_supabase_public_url_accepts_configured_origin() -> None:
    assert is_supabase_public_url(f"{supabase_public_origin()}/storage/v1/object/public/bucket/x.mtp")


def test_is_supabase_public_url_rejects_foreign_origin() -> None:
    assert not is_supabase_public_url("https://example.com/some/pot.dat")
    assert not is_supabase_public_url("https://evil.supabase.co.attacker.io/storage/v1/object/public/x")
    assert not is_supabase_public_url("http://169.254.169.254/latest/meta-data")


def test_is_supabase_public_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NFM_SUPABASE_PUBLIC_ORIGIN", "https://stub.supabase.co/")
    assert supabase_public_origin() == "https://stub.supabase.co"
    assert is_supabase_public_url("https://stub.supabase.co/storage/v1/object/public/x")
    assert not is_supabase_public_url(
        "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/x"
    )


# ---------------------------------------------------------------------------
# validate_persistable_file_url — ingestion shape contract (BUG-37 AC #4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/potentials/14607d0a-1a7b-49fd-9b22-1cd5671864c8/file",
        "/uploads/abc.tersoff",
        "/storage/v1/object/public/potentials/huda/Ag2S_MTP.mtp",
        "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/x.eam.fs",
    ],
)
def test_validate_accepts_allowed_forms(url: str) -> None:
    assert validate_persistable_file_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "/app/uploads/Fe_Mendelev_2007v2.eam.fs",
        "/app/uploads/x",
        "/etc/passwd",
        "/var/log/nfmd/prod-migrations.log",
        "file:///app/uploads/x.eam.fs",
        "C:\\app\\uploads\\x.eam.fs",
        "\\\\server\\share\\x.eam.fs",
        "bare.eam.alloy",
        "/uploads/",  # empty key after prefix
    ],
)
def test_validate_rejects_container_and_filesystem_paths(url: str) -> None:
    with pytest.raises(PotentialUploadError) as excinfo:
        validate_persistable_file_url(url)
    # The error must be self-explanatory for API consumers.
    assert "file_url" in excinfo.value.message


def test_validate_rejects_backslash_paths() -> None:
    with pytest.raises(PotentialUploadError):
        validate_persistable_file_url("https://example.com/a\\b.eam.fs")


def test_validate_accepts_none_and_empty() -> None:
    """Blank file_url is legal (rows without files)."""
    assert validate_persistable_file_url(None) is None
    assert validate_persistable_file_url("") is None


# ---------------------------------------------------------------------------
# download_count helpers (#1154 item 3 — atomic single-statement bump)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dialect", "expected_fragments"),
    [
        ("postgresql", ["jsonb_set", "CAST(:pid AS uuid)", "::json"]),
        ("sqlite", ["json_set", "$.download_count", "json_extract"]),
    ],
)
def test_download_count_update_sql_per_dialect(dialect: str, expected_fragments) -> None:
    sql = download_count_update_sql(dialect)
    for fragment in expected_fragments:
        assert fragment in sql
    # Both branches bump inside a single UPDATE — no read-modify-write race.
    assert sql.strip().upper().startswith("UPDATE POTENTIALS")
    assert "SELECT" not in sql.upper()


def test_download_count_update_sql_compiles_on_both_dialects() -> None:
    from sqlalchemy import text
    from sqlalchemy.dialects import postgresql, sqlite

    for dialect in (postgresql.dialect(), sqlite.dialect()):
        compiled = text(download_count_update_sql(dialect.name)).compile(dialect=dialect)
        assert ":pid" in str(compiled) or "%(pid)s" in str(compiled) or "?" in str(compiled)


def test_download_count_bind_value_matches_dialect_storage() -> None:
    """PG compares hyphenated text via CAST; SQLite stores Uuid as 32-hex."""
    pid = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
    assert download_count_bind_value("postgresql", pid) == str(pid)
    assert download_count_bind_value("sqlite", pid) == pid.hex
    # String ids normalize too (endpoint receives UUID, be lenient anyway).
    assert download_count_bind_value("sqlite", str(pid)) == pid.hex
