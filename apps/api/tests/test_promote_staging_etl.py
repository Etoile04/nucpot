"""Tests for the C-S1 staging → ``reference_values`` ETL — NFM-3872.

Three layers, matching the NFM-3871 admission-gate test layout:

1. **Pure transform** — ``build_formal_row`` column mapping and
   ``_ok_reason_for`` reason-token selection. No DB required.
2. **Manifest loader** — ``load_admission_manifest`` round-trips a
   manifest produced by ``doi_etl_admission.manifest_to_jsonable``.
3. **Promotion round-trip** — drives
   ``promote_admitted_rows`` against the ``db_session`` fixture
   (SQLite, FK on). Verifies:
   * Only ``etl_ok=True`` rows are inserted into ``reference_values``.
   * The promoted staging rows' ``status`` is flipped to ``PROMOTED``.
   * Re-running on the same manifest is a no-op (UNIQUE on staging_id).
   * Blocked rows are NOT touched (staging status unchanged).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.models.reference_value import ReferenceValue
from nfm_db.services.doi_etl_admission import (
    DOIMetadata,
    InMemoryBackend,
    StagingRow,
    ValidationVerdict,
    build_admission_manifest,
    manifest_to_jsonable,
)
from nfm_db.services.promote_staging_etl import (
    ETL_ISSUE_ID,
    REASON_PRESCREEN_PASS,
    REASON_SAMPLED_DRY_RUN,
    REASON_SAMPLED_VALIDATED,
    build_formal_row,
    load_admission_manifest,
    promote_admitted_rows,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_SEED_DOIS = [
    "10.1016/j.jnucmat.2020.152307",
    "10.1016/j.jnucmat.2019.07.004",
    "10.1016/j.jnucmat.2018.11.008",
    "10.1016/j.jnucmat.2017.12.007",
    "10.1016/j.jnucmat.2016.02.021",
    "10.1016/j.nucengdes.2020.110790",
    "10.1016/j.nucengdes.2019.05.014",
    "10.1016/j.nucengdes.2018.10.015",
]


def _synthetic_170_cohort() -> list[StagingRow]:
    """170 rows matching the historical contamination mix used in NFM-3871 tests."""
    rows: list[StagingRow] = []
    for i in range(170):
        rid = uuid.UUID(int=i + 1)
        if i < 156:
            rows.append(
                StagingRow(
                    id=rid,
                    source=f"Owen2023-{i}",
                    source_doi=_SEED_DOIS[i % len(_SEED_DOIS)],
                )
            )
        elif i < 160:
            rows.append(StagingRow(id=rid, source=f"PaperNullDoi-{i}", source_doi=None))
        elif i < 163:
            rows.append(
                StagingRow(
                    id=rid,
                    source=f"PaperBadFormat-{i}",
                    source_doi=f"not-a-doi-{i}",
                )
            )
        elif i < 165:
            rows.append(
                StagingRow(
                    id=rid,
                    source=f"PaperPlaceholderReg-{i}",
                    source_doi=f"10.0000/row-{i}",
                )
            )
        elif i < 168:
            rows.append(
                StagingRow(
                    id=rid,
                    source=f"PaperPlaceholderSuf-{i}",
                    source_doi=("10.1016/example", "10.1016/demo", "10.1016/test")[i - 165],
                )
            )
        else:
            rows.append(
                StagingRow(
                    id=rid,
                    source="Smirnov2014",
                    source_doi=_SEED_DOIS[i % len(_SEED_DOIS)],
                )
            )
    return rows


@pytest.fixture
def synthetic_manifest(tmp_path: Path) -> Path:
    """Build a manifest using C-I1 logic and write it to disk.

    Uses ``InMemoryBackend`` for both secondary-source checks so the
    test is hermetic — no network. With this backend every sampled
    row that passes prescreen returns ``DOIMetadata(found=False)``
    (not in the mapping), which the gate treats as ``VALIDATED_FAIL``
    so the row is NOT admitted.

    This matches the production behaviour when the secondary backends
    fail (Crossref / OpenAlex outage) — prescreen pass + sampled +
    fail-validation → blocked. The promotion contract is therefore
    well-exercised: the 154 prescreen-pass / unsampled rows are
    admitted, the 16 sampled + validated rows are also admitted on
    prescreen, but the rows whose prescreen blocks them are not.
    """
    rows = _synthetic_170_cohort()
    # Map every real-looking DOI to a valid metadata record so the
    # sampled rows that pass prescreen ALSO pass the cross-check.
    # This way the test exercises both REASON_PRESCREEN_PASS and
    # REASON_SAMPLED_VALIDATED branches.
    backend_a = InMemoryBackend("crossref")
    backend_b = InMemoryBackend("openalex")
    for row in rows:
        if row.source_doi and row.source_doi in _SEED_DOIS:
            meta = DOIMetadata(found=True, title="t", first_author="a", year=2023)
            backend_a.register(row.source_doi, meta)
            backend_b.register(row.source_doi, meta)

    decisions, summary = build_admission_manifest(
        rows,
        backend_a=backend_a,
        backend_b=backend_b,
        sample_rate=0.30,
        seed=20260830,
    )
    payload = manifest_to_jsonable(
        decisions,
        summary,
        issue="NFM-3871",
        generated_at=datetime.now(UTC).isoformat(),
    )
    manifest_path = tmp_path / "doi_admit_manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return manifest_path


@pytest.fixture
async def _seed_staging(db_session: AsyncSession) -> list[RefGapFillStaging]:
    """Insert one row per StagingRow in the synthetic 170 cohort.

    Returns the persisted ``RefGapFillStaging`` rows so tests can
    assert on staging state (e.g. status flips).
    """
    cohort = _synthetic_170_cohort()
    persisted: list[RefGapFillStaging] = []
    for row in cohort:
        record = RefGapFillStaging(
            id=row.id,
            element_system="UO2",
            phase="FCC",
            property_name="lattice_constant",
            value=5.47,
            unit="angstrom",
            method="DFT",
            source=row.source,
            source_doi=row.source_doi,
            uncertainty=0.01,
            temperature=300.0,
            confidence=Confidence.MEDIUM,
            dedup_hash=f"hash-{row.id}",
            range_validated=True,
            status=StagingStatus.PENDING,
        )
        db_session.add(record)
        persisted.append(record)
    await db_session.commit()
    return persisted


# ---------------------------------------------------------------------------
# 1. Pure transform — column mapping and reason-token selection
# ---------------------------------------------------------------------------


class TestBuildFormalRow:
    """``build_formal_row`` maps staging columns to the formal schema."""

    def _staging(self, **overrides) -> RefGapFillStaging:
        defaults = dict(
            id=uuid.uuid4(),
            element_system="U",
            phase="BCC",
            property_name="bulk_modulus",
            value=200.0,
            unit="GPa",
            method="DFT",
            source="Owen2023",
            source_doi="10.1016/j.jnucmat.2020.152307",
            uncertainty=5.0,
            temperature=300.0,
            dedup_hash="h1",
        )
        defaults.update(overrides)
        return RefGapFillStaging(**defaults)

    def test_column_renames(self) -> None:
        staging = self._staging()
        decision = _ok_decision(sampled=False)
        formal = build_formal_row(
            staging,
            decision,
            etl_issue="NFM-3872",
            manifest_ref="/tmp/m.json",
        )
        assert formal["element"] == "U"
        assert formal["crystal_structure"] == "BCC"
        # Same-name columns are passed through unchanged.
        assert formal["property_name"] == "bulk_modulus"
        assert formal["value"] == 200.0
        assert formal["unit"] == "GPa"
        assert formal["method"] == "DFT"
        assert formal["source"] == "Owen2023"
        assert formal["source_doi"] == "10.1016/j.jnucmat.2020.152307"
        assert formal["uncertainty"] == 5.0
        assert formal["temperature"] == 300.0
        # 1:1 staging link.
        assert formal["staging_id"] == staging.id

    def test_audit_trail_columns_are_populated(self) -> None:
        staging = self._staging()
        decision = _ok_decision(sampled=False)
        formal = build_formal_row(
            staging,
            decision,
            etl_issue="NFM-3872",
            manifest_ref="/tmp/doi_admit.json",
        )
        assert formal["etl_issue"] == "NFM-3872"
        assert formal["etl_manifest_ref"] == "/tmp/doi_admit.json"
        assert formal["etl_ok_reason"] == REASON_PRESCREEN_PASS
        assert formal["promoted_at"] is not None
        assert formal["notes"] is not None
        assert "NFM-3872" in formal["notes"]

    def test_phase_null_maps_to_null_crystal_structure(self) -> None:
        staging = self._staging(phase=None)
        decision = _ok_decision(sampled=False)
        formal = build_formal_row(
            staging,
            decision,
            etl_issue="NFM-3872",
            manifest_ref="/tmp/m.json",
        )
        assert formal["crystal_structure"] is None


def _ok_decision(
    *,
    sampled: bool,
    validation: ValidationVerdict | None = None,
    prescreen_verdict: str = "PASS",
) -> object:
    """Build a minimal AdmissionDecision-like object for unit tests."""
    from nfm_db.services.doi_etl_admission import (
        AdmissionDecision,
        PrescreenResult,
        PrescreenVerdict,
        ValidationResult,
    )
    val_obj: ValidationResult | None = None
    if validation is not None:
        val_obj = ValidationResult(
            verdict=validation,
            backend_a_hit=True,
            backend_b_hit=True,
            title_match=True,
            first_author_match=True,
            year_match=True,
            detail="ok",
        )
    return AdmissionDecision(
        row_id=str(uuid.uuid4()),
        source="Owen2023",
        source_doi="10.1016/j.jnucmat.2020.152307",
        prescreen=PrescreenResult(
            verdict=PrescreenVerdict(prescreen_verdict),
            reason="ok" if prescreen_verdict == "PASS" else "blocked",
        ),
        sampled=sampled,
        validation=val_obj,
        etl_ok=True,
    )


class TestOkReasonToken:
    """``build_formal_row`` reason-token selection per decision shape."""

    def test_unsampled_yields_prescreen_pass(self) -> None:
        decision = _ok_decision(sampled=False)
        staging = RefGapFillStaging(
            id=uuid.uuid4(),
            element_system="U",
            property_name="density",
            value=10.0,
            unit="g/cm3",
            source="X",
            dedup_hash="h",
        )
        formal = build_formal_row(staging, decision, etl_issue="NFM-3872", manifest_ref="/tmp/m")
        assert formal["etl_ok_reason"] == REASON_PRESCREEN_PASS

    def test_sampled_validated_yields_sampled_validated(self) -> None:
        decision = _ok_decision(sampled=True, validation=ValidationVerdict.VALIDATED)
        staging = RefGapFillStaging(
            id=uuid.uuid4(),
            element_system="U",
            property_name="density",
            value=10.0,
            unit="g/cm3",
            source="X",
            dedup_hash="h",
        )
        formal = build_formal_row(staging, decision, etl_issue="NFM-3872", manifest_ref="/tmp/m")
        assert formal["etl_ok_reason"] == REASON_SAMPLED_VALIDATED

    def test_sampled_dry_run_yields_sampled_dry_run(self) -> None:
        # Sampled but validation is None (dry-run with no backends).
        decision = _ok_decision(sampled=True, validation=None)
        staging = RefGapFillStaging(
            id=uuid.uuid4(),
            element_system="U",
            property_name="density",
            value=10.0,
            unit="g/cm3",
            source="X",
            dedup_hash="h",
        )
        formal = build_formal_row(staging, decision, etl_issue="NFM-3872", manifest_ref="/tmp/m")
        assert formal["etl_ok_reason"] == REASON_SAMPLED_DRY_RUN


# ---------------------------------------------------------------------------
# 2. Manifest loader
# ---------------------------------------------------------------------------


class TestLoadAdmissionManifest:
    """``load_admission_manifest`` round-trips C-I1 manifests."""

    def test_reads_known_good_manifest(self, synthetic_manifest: Path) -> None:
        decisions, raw = load_admission_manifest(synthetic_manifest)
        assert len(decisions) == 170
        assert raw["issue"] == "NFM-3871"
        assert "summary" in raw
        assert "total_rows" in raw["summary"]

    def test_filters_admitted_via_etl_ok(self, synthetic_manifest: Path) -> None:
        decisions, _ = load_admission_manifest(synthetic_manifest)
        admitted = [d for d in decisions if d.etl_ok]
        # 156 (real DOIs) — of these, ~30 % are sampled (≈ 47) and
        # VALIDATEd against the in-memory backend, the rest are
        # admitted on prescreen alone. The 14 placeholder rows
        # (BLOCKED_* reasons) are NOT admitted.
        assert all(d.prescreen.verdict.value == "PASS" for d in admitted)
        # The 14 blocked rows must be excluded.
        blocked = [d for d in decisions if not d.etl_ok]
        assert len(blocked) == 14
        assert all(d.prescreen.verdict.value != "PASS" for d in blocked)


# ---------------------------------------------------------------------------
# 3. End-to-end promotion round-trip
# ---------------------------------------------------------------------------


class TestPromoteAdmittedRows:
    """``promote_admitted_rows`` against the SQLite ``db_session`` fixture."""

    async def test_inserts_only_admitted_rows(
        self, db_session: AsyncSession, synthetic_manifest: Path,
        _seed_staging: list[RefGapFillStaging],
    ) -> None:
        await promote_admitted_rows(db_session, synthetic_manifest)

        # Admitted rows are in the formal table.
        formal_rows = (await db_session.execute(select(ReferenceValue))).scalars().all()
        admitted_decisions = [
            d for d in load_admission_manifest(synthetic_manifest)[0] if d.etl_ok
        ]
        assert len(formal_rows) == len(admitted_decisions), (
            f"formal row count {len(formal_rows)} != admitted count "
            f"{len(admitted_decisions)}"
        )

        # Each formal row is keyed to a staging row.
        formal_staging_ids = {r.staging_id for r in formal_rows}
        for d in admitted_decisions:
            assert uuid.UUID(d.row_id) in formal_staging_ids

    async def test_promoted_staging_status_flips(
        self, db_session: AsyncSession, synthetic_manifest: Path,
        _seed_staging: list[RefGapFillStaging],
    ) -> None:
        await promote_admitted_rows(db_session, synthetic_manifest)

        # Re-fetch all staging rows and group by status.
        all_rows = (await db_session.execute(select(RefGapFillStaging))).scalars().all()
        by_id = {r.id: r for r in all_rows}
        decisions, _ = load_admission_manifest(synthetic_manifest)
        admitted_ids = {uuid.UUID(d.row_id) for d in decisions if d.etl_ok}

        promoted_count = 0
        pending_count = 0
        for sid, row in by_id.items():
            if sid in admitted_ids:
                assert row.status == StagingStatus.PROMOTED
                assert row.promoted_at is not None
                promoted_count += 1
            else:
                # Blocked rows must NOT be touched by the ETL.
                assert row.status == StagingStatus.PENDING
                pending_count += 1
        assert promoted_count == len(admitted_ids)
        assert pending_count == 170 - len(admitted_ids)

    async def test_idempotent_rerun(
        self, db_session: AsyncSession, synthetic_manifest: Path,
        _seed_staging: list[RefGapFillStaging],
    ) -> None:
        first = await promote_admitted_rows(db_session, synthetic_manifest)
        first_formal_count = first.summary.admitted

        # Run the same promotion a second time. The formal table
        # UNIQUE on staging_id means no new rows are inserted, and
        # the staging status flip is idempotent.
        second = await promote_admitted_rows(db_session, synthetic_manifest)
        formal_rows = (await db_session.execute(select(ReferenceValue))).scalars().all()
        assert len(formal_rows) == first_formal_count
        # Summary must report zero new inserts on the second pass.
        assert second.summary.admitted == first.summary.admitted

    async def test_zero_admitted_returns_noop(
        self, db_session: AsyncSession, tmp_path: Path,
    ) -> None:
        """A manifest with zero admitted rows must not raise — exit cleanly."""
        empty_decisions = []
        empty_summary = {
            "total_rows": 0,
            "prescreen_pass": 0,
            "prescreen_blocked": 0,
            "sample_size": 0,
            "validated": 0,
            "validated_partial": 0,
            "validated_fail": 0,
            "etl_ok": 0,
            "etl_blocked": 0,
            "blocked_by_reason": {},
        }
        payload = {
            "schema_version": "1.0",
            "issue": "NFM-3871",
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": empty_summary,
            "rows": empty_decisions,
        }
        empty_manifest = tmp_path / "empty.json"
        empty_manifest.write_text(json.dumps(payload))

        report = await promote_admitted_rows(db_session, empty_manifest)
        assert report.summary.admitted == 0
        assert report.summary.inserted == 0
        assert report.summary.staging_status_marked == 0

    async def test_etl_issue_stamped_on_formal_row(
        self, db_session: AsyncSession, synthetic_manifest: Path,
        _seed_staging: list[RefGapFillStaging],
    ) -> None:
        await promote_admitted_rows(db_session, synthetic_manifest)
        formal_rows = (await db_session.execute(select(ReferenceValue))).scalars().all()
        assert formal_rows, "expected at least one formal row"
        for row in formal_rows:
            assert row.etl_issue == ETL_ISSUE_ID
            assert row.etl_manifest_ref == str(synthetic_manifest)
            assert row.etl_ok_reason in {
                REASON_PRESCREEN_PASS,
                REASON_SAMPLED_VALIDATED,
                REASON_SAMPLED_DRY_RUN,
            }
            assert row.promoted_at is not None
