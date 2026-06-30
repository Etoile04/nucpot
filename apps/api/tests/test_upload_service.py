"""Unit tests for upload_service (NFM-582).

Tests for file validation, filename sanitization, hash computation,
upload directory resolution, metadata validation, and CRUD operations.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.schemas.potential import PotentialCreateRequest
from nfm_db.services.upload_service import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    PotentialNameConflictError,
    PotentialNotFoundError,
    PotentialUploadError,
    _compute_hash,
    _default_upload_dir,
    _sanitize_filename,
    _validate_extension,
    _validate_metadata,
    _validate_size,
    attach_potential_file,
    create_potential,
    get_upload_dir,
)

# ---------------------------------------------------------------------------
# _validate_extension
# ---------------------------------------------------------------------------


class TestValidateExtension:
    """Tests for _validate_extension."""

    def test_accepts_eam_alloy(self) -> None:
        """GIVEN filename ends with .eam.alloy, THEN no exception."""
        _validate_extension("potential.eam.alloy")

    def test_accepts_eam(self) -> None:
        """GIVEN filename ends with .eam, THEN no exception."""
        _validate_extension("pot.eam")

    def test_accepts_json(self) -> None:
        """GIVEN filename ends with .json, THEN no exception."""
        _validate_extension("data.json")

    def test_accepts_tar_gz(self) -> None:
        """GIVEN filename ends with .tar.gz, THEN no exception."""
        _validate_extension("archive.tar.gz")

    def test_accepts_gz(self) -> None:
        """GIVEN filename ends with .gz, THEN no exception."""
        _validate_extension("file.gz")

    def test_rejects_unsupported_extension(self) -> None:
        """GIVEN filename ends with .exe, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError, match="Unsupported file extension"):
            _validate_extension("malware.exe")

    def test_rejects_no_extension(self) -> None:
        """GIVEN filename has no extension, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError):
            _validate_extension("README")

    def test_case_insensitive(self) -> None:
        """GIVEN uppercase extension, THEN still accepted."""
        _validate_extension("POT.EAM.ALLOY")
        _validate_extension("POT.JSON")

    @pytest.mark.parametrize("ext", list(ALLOWED_EXTENSIONS))
    def test_all_allowed_extensions_accepted(self, ext: str) -> None:
        """Every extension in ALLOWED_EXTENSIONS is accepted."""
        _validate_extension(f"test{ext}")


# ---------------------------------------------------------------------------
# _validate_size
# ---------------------------------------------------------------------------


class TestValidateSize:
    """Tests for _validate_size."""

    def test_accepts_small_file(self) -> None:
        """GIVEN file under 50MB, THEN no exception."""
        _validate_size(1024)

    def test_accepts_exactly_50mb(self) -> None:
        """GIVEN file exactly 50MB, THEN no exception."""
        _validate_size(MAX_FILE_SIZE)

    def test_rejects_over_50mb(self) -> None:
        """GIVEN file over 50MB, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError, match="File too large"):
            _validate_size(MAX_FILE_SIZE + 1)

    def test_error_message_includes_size_mb(self) -> None:
        """GIVEN oversized file, THEN error includes size in MB."""
        size_mb = 60
        size_bytes = size_mb * 1024 * 1024
        with pytest.raises(PotentialUploadError, match=f"{size_mb:.1f}MB"):
            _validate_size(size_bytes)


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    """Tests for _sanitize_filename."""

    def test_leaves_clean_name_unchanged(self) -> None:
        """GIVEN filename with only safe chars, THEN unchanged."""
        assert _sanitize_filename("potential.eam") == "potential.eam"

    def test_replaces_spaces_with_underscore(self) -> None:
        """GIVEN filename with spaces, THEN spaces become underscores."""
        assert _sanitize_filename("my potential.eam") == "my_potential.eam"

    def test_replaces_special_characters(self) -> None:
        """GIVEN filename with special chars, THEN replaced with underscore."""
        result = _sanitize_filename("file@name#$.eam")
        assert "@" not in result
        assert "#" not in result
        assert "$" not in result

    def test_preserves_dots_dashes_and_underscores(self) -> None:
        """GIVEN filename with dots, dashes, underscores, THEN preserved."""
        assert _sanitize_filename("my-file_name.eam.alloy") == "my-file_name.eam.alloy"

    def test_empty_filename(self) -> None:
        """GIVEN empty filename, THEN returns empty string."""
        assert _sanitize_filename("") == ""


# ---------------------------------------------------------------------------
# _compute_hash
# ---------------------------------------------------------------------------


class TestComputeHash:
    """Tests for _compute_hash."""

    def test_returns_sha256_hex(self) -> None:
        """GIVEN data bytes, THEN returns 64-char hex string."""
        result = _compute_hash(b"test data")
        assert len(result) == 64
        # All hex chars
        int(result, 16)

    def test_deterministic(self) -> None:
        """GIVEN same data, THEN same hash."""
        h1 = _compute_hash(b"hello")
        h2 = _compute_hash(b"hello")
        assert h1 == h2

    def test_different_for_different_data(self) -> None:
        """GIVEN different data, THEN different hash."""
        h1 = _compute_hash(b"hello")
        h2 = _compute_hash(b"world")
        assert h1 != h2

    def test_empty_data(self) -> None:
        """GIVEN empty bytes, THEN returns valid sha256."""
        result = _compute_hash(b"")
        assert len(result) == 64


# ---------------------------------------------------------------------------
# upload directory
# ---------------------------------------------------------------------------


class TestUploadDir:
    """Tests for get_upload_dir and _default_upload_dir."""

    def test_get_upload_dir_returns_override(self, tmp_path: Path) -> None:
        """GIVEN override is set, THEN get_upload_dir returns override."""
        import nfm_db.services.upload_service as mod

        mod._UPLOAD_DIR_OVERRIDE = tmp_path
        try:
            assert get_upload_dir() == tmp_path
        finally:
            mod._UPLOAD_DIR_OVERRIDE = None

    def test_get_upload_dir_without_override(self) -> None:
        """GIVEN no override, THEN get_upload_dir calls _default_upload_dir."""
        import nfm_db.services.upload_service as mod

        mod._UPLOAD_DIR_OVERRIDE = None
        result = get_upload_dir()
        # Should end with uploads
        assert result.name == "uploads"

    def test_default_upload_dir_resolves_from_file(self) -> None:
        """GIVEN no override, THEN path contains apps/web/public/uploads."""
        result = _default_upload_dir()
        assert "apps" in result.parts
        assert "web" in result.parts
        assert "public" in result.parts
        assert "uploads" in result.parts


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------


class TestExceptions:
    """Tests for upload service exception classes."""

    def test_upload_error_has_400_status(self) -> None:
        """GIVEN PotentialUploadError, THEN status_code is 400."""
        exc = PotentialUploadError("bad file")
        assert exc.status_code == 400
        assert exc.message == "bad file"

    def test_name_conflict_has_409_status(self) -> None:
        """GIVEN PotentialNameConflictError, THEN status_code is 409."""
        exc = PotentialNameConflictError("exists")
        assert exc.status_code == 409

    def test_not_found_has_404_status(self) -> None:
        """GIVEN PotentialNotFoundError, THEN status_code is 404."""
        exc = PotentialNotFoundError("missing")
        assert exc.status_code == 404


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_payload(**overrides: object) -> PotentialCreateRequest:
    """Build a valid PotentialCreateRequest payload for testing."""

    defaults = {
        "name": "EAM_U_Test",
        "type": "EAM",
        "elements": ["U"],
        "system_name": "U",
        "description": "Test potential",
        "license_type": "own_work",
    }
    defaults.update(overrides)
    return PotentialCreateRequest(**defaults)


# ---------------------------------------------------------------------------
# _validate_metadata
# ---------------------------------------------------------------------------


class TestValidateMetadata:
    """Tests for _validate_metadata."""

    def test_valid_own_work_passes(self) -> None:
        """GIVEN valid own_work payload, THEN no exception."""
        _validate_metadata(_make_payload())

    def test_valid_open_license_passes(self) -> None:
        """GIVEN valid open_license with detail, THEN no exception."""
        _validate_metadata(
            _make_payload(
                license_type="open_license",
                license_detail="CC-BY-4.0",
            )
        )

    def test_valid_author_permission_passes(self) -> None:
        """GIVEN valid author_permission with auth file, THEN no exception."""
        _validate_metadata(
            _make_payload(
                license_type="author_permission",
                auth_file_path="/uploads/auth.pdf",
            )
        )

    def test_rejects_empty_name(self) -> None:
        """GIVEN empty name, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError, match="required"):
            _validate_metadata(_make_payload(name=""))

    def test_rejects_empty_type(self) -> None:
        """GIVEN empty type, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError, match="required"):
            _validate_metadata(_make_payload(type=""))

    def test_rejects_empty_elements(self) -> None:
        """GIVEN empty elements, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError, match="required"):
            _validate_metadata(_make_payload(elements=[]))

    def test_rejects_empty_system_name(self) -> None:
        """GIVEN empty system_name, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError, match="required"):
            _validate_metadata(_make_payload(system_name=""))

    def test_rejects_empty_description(self) -> None:
        """GIVEN empty description, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError, match="required"):
            _validate_metadata(_make_payload(description=""))

    def test_rejects_invalid_license_type(self) -> None:
        """GIVEN invalid license_type, THEN raises PotentialUploadError."""
        with pytest.raises(PotentialUploadError, match="license_type is required"):
            _validate_metadata(_make_payload(license_type="invalid"))

    def test_rejects_author_permission_without_auth_file(self) -> None:
        """GIVEN author_permission without auth_file_path, THEN raises."""
        with pytest.raises(
            PotentialUploadError,
            match="Authorization proof file is required",
        ):
            _validate_metadata(_make_payload(license_type="author_permission"))

    def test_rejects_open_license_without_detail(self) -> None:
        """GIVEN open_license without license_detail, THEN raises."""
        with pytest.raises(
            PotentialUploadError,
            match="License name",
        ):
            _validate_metadata(
                _make_payload(license_type="open_license")
            )


# ---------------------------------------------------------------------------
# create_potential
# ---------------------------------------------------------------------------


class TestCreatePotential:
    """Tests for create_potential DB operations."""

    @pytest.mark.asyncio
    async def test_creates_potential_successfully(self, db_session: AsyncSession) -> None:
        """GIVEN valid payload, THEN potential is created with correct fields."""
        payload = _make_payload()
        result = await create_potential(db_session, payload)

        assert result.id is not None
        assert result.name == "EAM_U_Test"
        assert result.type == "EAM"
        assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_sets_extra_fields(self, db_session: AsyncSession) -> None:
        """GIVEN valid payload, THEN extra dict has license and verification fields."""
        payload = _make_payload(license_type="open_license", license_detail="CC-BY-4.0")
        result = await create_potential(db_session, payload)

        assert result.extra["license_type"] == "open_license"
        assert result.extra["license_detail"] == "CC-BY-4.0"
        assert result.extra["verification_status"] == "unverified"

    @pytest.mark.asyncio
    async def test_rejects_duplicate_name(self, db_session: AsyncSession) -> None:
        """GIVEN name already exists, THEN raises PotentialNameConflictError."""
        payload = _make_payload()
        await create_potential(db_session, payload)

        with pytest.raises(PotentialNameConflictError, match="already exists"):
            await create_potential(db_session, payload)

    @pytest.mark.asyncio
    async def test_rejects_invalid_metadata(self, db_session: AsyncSession) -> None:
        """GIVEN invalid payload, THEN raises PotentialUploadError."""
        payload = _make_payload(name="")
        with pytest.raises(PotentialUploadError):
            await create_potential(db_session, payload)

    @pytest.mark.asyncio
    async def test_display_name_defaults_to_name(self, db_session: AsyncSession) -> None:
        """GIVEN no display_name, THEN display_name equals name."""
        payload = _make_payload(display_name=None)
        result = await create_potential(db_session, payload)
        assert result.display_name == result.name

    @pytest.mark.asyncio
    async def test_custom_display_name(self, db_session: AsyncSession) -> None:
        """GIVEN custom display_name, THEN it is stored."""
        payload = _make_payload(display_name="Custom Name")
        result = await create_potential(db_session, payload)
        assert result.display_name == "Custom Name"


# ---------------------------------------------------------------------------
# attach_potential_file
# ---------------------------------------------------------------------------


class TestAttachPotentialFile:
    """Tests for attach_potential_file."""

    @pytest.mark.asyncio
    async def test_attach_file_successfully(
        self, db_session: AsyncSession, tmp_path: Path,
    ) -> None:
        """GIVEN valid file and existing potential, THEN file is written and DB updated."""
        from nfm_db.models import Potential

        # Create a potential first
        potential = Potential(
            name="attach_test",
            type="EAM",
            elements=["U"],
            status="pending",
            lammps_config={},
            extra={},
        )
        db_session.add(potential)
        await db_session.commit()
        await db_session.refresh(potential)

        data = b"test file content for potential"
        result = await attach_potential_file(
            db_session,
            upload_dir=tmp_path,
            potential_id=potential.id,
            filename="potential.eam",
            data=data,
        )

        assert result["file_name"] == "potential.eam"
        assert result["file_hash"] == _compute_hash(data)
        assert result["file_size"] == len(data)
        assert "/uploads/" in result["file_url"]

    @pytest.mark.asyncio
    async def test_attach_file_sanitizes_filename(
        self, db_session: AsyncSession, tmp_path: Path,
    ) -> None:
        """GIVEN filename with special chars, THEN sanitized in file URL."""
        from nfm_db.models import Potential

        potential = Potential(
            name="sanitize_test",
            type="EAM",
            elements=["U"],
            status="pending",
            lammps_config={},
            extra={},
        )
        db_session.add(potential)
        await db_session.commit()
        await db_session.refresh(potential)

        result = await attach_potential_file(
            db_session,
            upload_dir=tmp_path,
            potential_id=potential.id,
            filename="my file.eam",
            data=b"data",
        )

        assert "my_file.eam" in result["file_url"]

    @pytest.mark.asyncio
    async def test_attach_file_rejects_bad_extension(
        self, db_session: AsyncSession, tmp_path: Path,
    ) -> None:
        """GIVEN file with unsupported extension, THEN raises PotentialUploadError."""
        from nfm_db.models import Potential

        potential = Potential(
            name="ext_test",
            type="EAM",
            elements=["U"],
            status="pending",
            lammps_config={},
            extra={},
        )
        db_session.add(potential)
        await db_session.commit()
        await db_session.refresh(potential)

        with pytest.raises(PotentialUploadError, match="Unsupported"):
            await attach_potential_file(
                db_session,
                upload_dir=tmp_path,
                potential_id=potential.id,
                filename="file.exe",
                data=b"bad",
            )

    @pytest.mark.asyncio
    async def test_attach_file_rejects_oversize(
        self, db_session: AsyncSession, tmp_path: Path,
    ) -> None:
        """GIVEN file over 50MB, THEN raises PotentialUploadError."""
        from nfm_db.models import Potential

        potential = Potential(
            name="size_test",
            type="EAM",
            elements=["U"],
            status="pending",
            lammps_config={},
            extra={},
        )
        db_session.add(potential)
        await db_session.commit()
        await db_session.refresh(potential)

        with pytest.raises(PotentialUploadError, match="File too large"):
            await attach_potential_file(
                db_session,
                upload_dir=tmp_path,
                potential_id=potential.id,
                filename="big.eam",
                data=b"x" * (MAX_FILE_SIZE + 1),
            )

    @pytest.mark.asyncio
    async def test_attach_file_rejects_missing_potential(
        self, db_session: AsyncSession, tmp_path: Path,
    ) -> None:
        """GIVEN potential_id does not exist, THEN raises PotentialNotFoundError."""
        with pytest.raises(PotentialNotFoundError, match="not found"):
            await attach_potential_file(
                db_session,
                upload_dir=tmp_path,
                potential_id=uuid.uuid4(),
                filename="potential.eam",
                data=b"data",
            )
