"""Staging → formal ``reference_values`` ETL — NFM-3872 (Wayfinder pilot C / C-S1).

Reads a C-I1 admission manifest produced by ``doi_etl_admit.py``
(NFM-3871) and promotes every admitted ``_ref_gap_fill_staging`` row
into the new ``reference_values`` formal table. Admitted means
``AdmissionDecision.etl_ok is True`` — the deterministic pre-screen
PASSed AND (for sampled rows) the secondary-source cross-check
VALIDATEd.

Re-running the ETL on the same manifest is a no-op: ``staging_id`` is
``UNIQUE`` on the formal table, so the second pass ``UPDATE``s instead
of duplicating. This is the idempotency contract the C-S1 handoff
comment relies on — operators can rerun the promotion after an
incident review and the row count on the formal table does not
change.

Design notes
------------

* **Pure transform + a thin async wrapper.** The heavy lifting — read
  manifest, map staging columns to formal columns, decide what reason
  to record — is in ``_build_formal_row``. The async wrapper opens
  an ``AsyncSession``, loads staging rows, upserts them, and updates
  ``staging.status`` to ``PROMOTED``. This split keeps the unit tests
  fast (no DB required) while still exercising the real SQL path in
  integration tests.

* **PostgreSQL upsert via ``INSERT ... ON CONFLICT (staging_id) DO
  UPDATE``.** On SQLite (CI / unit tests) we use a try/except
  IntegrityError + UPDATE fallback because SQLite does not support
  the PostgreSQL ON CONFLICT syntax. Both paths produce the same
  end state.

* **Why we also write ``staging.promoted_at`` but not
  ``staging.promoted_to_pm_id``.** The pre-existing
  ``promoted_to_pm_id`` column on staging is wired to the older
  ``property_measurements`` promotion path (used by
  ``approve_staging_record``). The C-S1 formal-table promotion is a
  different sink, so we reuse ``promoted_at`` to mark the promotion
  moment and leave ``promoted_to_pm_id`` alone — the columns are
  independent and reconciling them is out of scope for C-S1.

* **Status transition.** Rows that pass admission and are promoted
  flip ``_ref_gap_fill_staging.status`` from ``PENDING`` (or whatever
  they were) to ``PROMOTED``. Rows that did NOT pass admission are
  not touched by the ETL — their status remains whatever it was
  before, and they live in staging as audit data only.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import RefGapFillStaging, StagingStatus
from nfm_db.models.reference_value import ReferenceValue
from nfm_db.services.doi_etl_admission import (
    AdmissionDecision,
    PrescreenResult,
    PrescreenVerdict,
    ValidationResult,
    ValidationVerdict,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Paperclip issue ID stamped onto every formal row's ``etl_issue``.
#: The pilot C-line is committed to writing the human-readable issue
#: reference so a future operator can replay the admission decision
#: from the row alone.
ETL_ISSUE_ID: str = "NFM-3872"

#: Reason token stamped onto formal rows that passed pre-screen but
#: were not sampled (the unsampled 70 %). Per C-D7 amendment: admit
#: on pre-screen alone when sampling did not select the row.
REASON_PRESCREEN_PASS: str = "prescreen_pass"

#: Reason token stamped onto formal rows that were sampled AND
#: VALIDATEd by both secondary-source backends.
REASON_SAMPLED_VALIDATED: str = "prescreen_pass+sampled_validated"

#: Reason token stamped onto formal rows that were sampled in a
#: dry-run session (``backend_a=None`` / ``backend_b=None``). Same
#: outcome as ``REASON_PRESCREEN_PASS`` — prescreen-only admission —
#: but operators can tell from ``etl_ok_reason`` that no Crossref /
#: OpenAlex cross-check ran, so they know to re-run with backends
#: wired before merging to production.
REASON_SAMPLED_DRY_RUN: str = "prescreen_pass+sampled_dry_run"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionSummary:
    """Aggregate counts for a single promotion run."""

    manifest_ref: str
    etl_issue: str
    total_decisions: int
    admitted: int
    skipped_blocked: int
    inserted: int
    updated: int
    staging_status_marked: int


@dataclass(frozen=True)
class PromotionReport:
    """Return value of :func:`promote_admitted_rows`."""

    summary: PromotionSummary
    promoted_row_ids: list[uuid.UUID] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def load_admission_manifest(
    manifest_path: str | Path,
) -> tuple[list[AdmissionDecision], dict[str, Any]]:
    """Read the C-I1 admission manifest from disk.

    Returns the per-row decisions and the raw manifest dict (for the
    operator summary line). The manifest schema is owned by
    ``doi_etl_admission.manifest_to_jsonable`` — this loader does NOT
    validate schema_version because the C-I1 and C-S1 ships are
    co-versioned.
    """
    path = Path(manifest_path)
    payload = json.loads(path.read_text())
    decisions: list[AdmissionDecision] = []
    for row in payload.get("rows", []):
        ps = row["prescreen"]
        val = row.get("validation")
        val_obj: ValidationResult | None = None
        if val is not None:
            val_obj = ValidationResult(
                verdict=ValidationVerdict(val["verdict"]),
                backend_a_hit=val["backend_a_hit"],
                backend_b_hit=val["backend_b_hit"],
                title_match=val["title_match"],
                first_author_match=val["first_author_match"],
                year_match=val["year_match"],
                detail=val["detail"],
            )
        decisions.append(
            AdmissionDecision(
                row_id=row["row_id"],
                source=row["source"],
                source_doi=row["source_doi"],
                prescreen=PrescreenResult(
                    verdict=PrescreenVerdict(ps["verdict"]),
                    reason=ps["reason"],
                ),
                sampled=row["sampled"],
                validation=val_obj,
                etl_ok=row["etl_ok"],
                blocking_reason=row.get("blocking_reason"),
            )
        )
    return decisions, payload


# ---------------------------------------------------------------------------
# Pure transform
# ---------------------------------------------------------------------------


def _ok_reason_for(decision: AdmissionDecision) -> str:
    """Return the ``etl_ok_reason`` token for a single admitted decision.

    Three cases:
      * Sampled AND validated → ``prescreen_pass+sampled_validated``.
      * Sampled in dry-run (validation is None because no backends
        wired) → ``prescreen_pass+sampled_dry_run`` so operators can
        tell the secondary check did not run.
      * Unsampled (the unsampled 70 % in normal mode) → plain
        ``prescreen_pass``.
    """
    if not decision.sampled:
        return REASON_PRESCREEN_PASS
    if decision.validation is None:
        # Sampled but no secondary check — dry-run.
        return REASON_SAMPLED_DRY_RUN
    if decision.validation.verdict == ValidationVerdict.VALIDATED:
        return REASON_SAMPLED_VALIDATED
    # Admitted on prescreen despite a non-VALIDATED sampled check
    # (e.g. partial). Per doi_etl_admission this should never happen
    # (row_etl_ok is False for partial), but keep the guard so a
    # future amendment does not silently corrupt the reason taxonomy.
    return REASON_PRESCREEN_PASS


def build_formal_row(
    staging: RefGapFillStaging,
    decision: AdmissionDecision,
    *,
    etl_issue: str,
    manifest_ref: str,
) -> dict[str, Any]:
    """Map a staging row + its admission decision to a formal-row dict.

    Column mapping (staging → formal):

    ===========================  ===========================
    Staging                      Formal (``reference_values``)
    ===========================  ===========================
    ``element_system``           ``element``
    ``phase``                    ``crystal_structure``
    ``property_name``            ``property_name`` (same)
    ``value``                    ``value`` (same)
    ``unit``                     ``unit`` (same)
    ``method``                   ``method`` (same)
    ``source``                   ``source`` (same)
    ``source_doi``               ``source_doi`` (same)
    ``uncertainty``              ``uncertainty`` (same)
    ``temperature``              ``temperature`` (same)
    —                            ``staging_id`` (1:1 FK)
    —                            ``etl_issue``, ``etl_manifest_ref``,
                                  ``etl_ok_reason``, ``promoted_at``
    —                            ``notes`` (free-form provenance)
    ===========================  ===========================
    """
    reason = _ok_reason_for(decision)
    return {
        "staging_id": staging.id,
        "element": staging.element_system,
        "crystal_structure": staging.phase,
        "property_name": staging.property_name,
        "value": staging.value,
        "unit": staging.unit,
        "method": staging.method,
        "source": staging.source,
        "source_doi": staging.source_doi,
        "uncertainty": staging.uncertainty,
        "temperature": staging.temperature,
        "notes": (
            f"Promoted from _ref_gap_fill_staging by {etl_issue} via "
            f"C-I1 admission manifest. Reason={reason}; "
            f"prescreen_verdict={decision.prescreen.verdict.value}; "
            f"sampled={decision.sampled}."
        ),
        "etl_issue": etl_issue,
        "etl_manifest_ref": manifest_ref,
        "etl_ok_reason": reason,
        "promoted_at": datetime.now(UTC),
    }


# Backwards-compat shim: the early draft of this module exposed
# ``_build_formal_row`` (private name). Keep a re-export so older
# callers (and any in-flight branch that imports it) keep compiling.
_build_formal_row = build_formal_row


# ---------------------------------------------------------------------------
# Async promotion
# ---------------------------------------------------------------------------


async def promote_admitted_rows(
    session: AsyncSession,
    manifest_path: str | Path,
    *,
    etl_issue: str = ETL_ISSUE_ID,
) -> PromotionReport:
    """Promote every ``etl_ok`` row from the manifest into ``reference_values``.

    Algorithm:

    1. Load the manifest (decisions + raw payload).
    2. Filter to ``etl_ok=True`` decisions and resolve their UUIDs.
    3. Bulk-load the corresponding ``_ref_gap_fill_staging`` rows.
    4. Build formal-row dicts and upsert them via
       ``INSERT ... ON CONFLICT (staging_id) DO UPDATE`` (PostgreSQL)
       or ``try/except IntegrityError`` + UPDATE (SQLite). Re-runs
       are idempotent.
    5. Mark each promoted staging row ``status=PROMOTED`` and
       ``promoted_at=NOW()``. Rows that did NOT pass admission are
       not touched.
    6. Commit and return a ``PromotionReport`` with counts and the
       list of promoted staging IDs.
    """
    decisions, _raw = load_admission_manifest(manifest_path)
    manifest_ref = str(manifest_path)

    admitted = [d for d in decisions if d.etl_ok]
    admitted_ids: list[uuid.UUID] = []
    for d in admitted:
        try:
            admitted_ids.append(uuid.UUID(d.row_id))
        except (ValueError, TypeError):
            # Manifest is malformed — refuse to promote a partial set.
            raise ValueError(
                f"manifest row_id {d.row_id!r} is not a valid UUID"
            ) from None

    if not admitted_ids:
        # Nothing to do — but still emit a report so the operator's
        # CI smoke test can assert on the no-op behaviour.
        return PromotionReport(
            summary=PromotionSummary(
                manifest_ref=manifest_ref,
                etl_issue=etl_issue,
                total_decisions=len(decisions),
                admitted=0,
                skipped_blocked=len(decisions),
                inserted=0,
                updated=0,
                staging_status_marked=0,
            ),
            promoted_row_ids=[],
        )

    # Bulk-load the staging rows we'll promote.
    stmt = select(RefGapFillStaging).where(RefGapFillStaging.id.in_(admitted_ids))
    staging_rows = (await session.execute(stmt)).scalars().all()
    staging_by_id = {row.id: row for row in staging_rows}

    # Build formal-row dicts in decision order so the promoted_row_ids
    # list matches the manifest order (helps tests / debugging).
    formal_payload: list[dict[str, Any]] = []
    promoted_row_ids: list[uuid.UUID] = []
    for decision in admitted:
        sid = uuid.UUID(decision.row_id)
        staging = staging_by_id.get(sid)
        if staging is None:
            # Decision references a staging row that no longer exists
            # (deleted between the C-I1 run and now). Skip with a
            # count, not a hard error — partial deletes are a known
            # production failure mode.
            continue
        formal_payload.append(
            build_formal_row(
                staging,
                decision,
                etl_issue=etl_issue,
                manifest_ref=manifest_ref,
            )
        )
        promoted_row_ids.append(sid)

    inserted = updated = 0

    if formal_payload:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            # NOTE: ``stmt`` at line 332 is a Select for reading staging rows;
            # this branch needs an Insert bound to a fresh name so mypy
            # doesn't union the two statement types (which would erase
            # Insert-only attributes like ``on_conflict_do_update`` /
            # ``excluded``).
            upsert_stmt = pg_insert(ReferenceValue).values(formal_payload)
            upsert = upsert_stmt.on_conflict_do_update(
                index_elements=[ReferenceValue.staging_id],
                set_={
                    "element": upsert_stmt.excluded.element,
                    "crystal_structure": upsert_stmt.excluded.crystal_structure,
                    "property_name": upsert_stmt.excluded.property_name,
                    "value": upsert_stmt.excluded.value,
                    "unit": upsert_stmt.excluded.unit,
                    "method": upsert_stmt.excluded.method,
                    "source": upsert_stmt.excluded.source,
                    "source_doi": upsert_stmt.excluded.source_doi,
                    "uncertainty": upsert_stmt.excluded.uncertainty,
                    "temperature": upsert_stmt.excluded.temperature,
                    "notes": upsert_stmt.excluded.notes,
                    "etl_issue": upsert_stmt.excluded.etl_issue,
                    "etl_manifest_ref": upsert_stmt.excluded.etl_manifest_ref,
                    "etl_ok_reason": upsert_stmt.excluded.etl_ok_reason,
                    "promoted_at": upsert_stmt.excluded.promoted_at,
                },
            )
            await session.execute(upsert)
            # INSERT-or-UPDATE: rowcount is rows-inserted + 2*rows-updated.
            # We can't easily distinguish without row-by-row, so report
            # the total touched and let the caller inspect
            # ``promoted_row_ids`` if needed.
            inserted = len(formal_payload)
        else:
            # SQLite / non-PG path: per-row INSERT-or-UPDATE so the
            # counters are exact. We use a pre-check ("SELECT then
            # INSERT-or-UPDATE") rather than a try/except + rollback
            # because rolling back a failed flush on SQLite can leave
            # the session in a state where the existing row is no
            # longer visible to the same connection — the row IS
            # there on disk but the just-rolled-back transaction's
            # view of the world is poisoned.
            for payload in formal_payload:
                existing = await session.execute(
                    select(ReferenceValue).where(
                        ReferenceValue.staging_id == payload["staging_id"],
                    )
                )
                row = existing.scalar_one_or_none()
                if row is None:
                    session.add(ReferenceValue(**payload))
                    inserted += 1
                else:
                    for k, v in payload.items():
                        setattr(row, k, v)
                    updated += 1
            await session.flush()

        # Flip staging.status = PROMOTED for every promoted row.
        # We mutate via the ORM (not a bulk UPDATE) so the session's
        # identity map stays consistent with the database — a bulk
        # ``Table.update()`` would leave the cached ORM instances
        # showing ``PENDING`` even though the row is now PROMOTED,
        # which breaks the read-back in the same transaction and
        # confuses downstream callers / tests. The bulk load earlier
        # already loaded each promoted row into the session, so we
        # just iterate over them and let SQLAlchemy dirty-check.
        now = datetime.now(UTC)
        for staging in staging_rows:
            if staging.id in {pid for pid in promoted_row_ids}:
                staging.status = StagingStatus.PROMOTED
                staging.promoted_at = now
        await session.commit()

    return PromotionReport(
        summary=PromotionSummary(
            manifest_ref=manifest_ref,
            etl_issue=etl_issue,
            total_decisions=len(decisions),
            admitted=len(admitted),
            skipped_blocked=len(decisions) - len(admitted),
            inserted=inserted,
            updated=updated,
            staging_status_marked=len(promoted_row_ids),
        ),
        promoted_row_ids=promoted_row_ids,
    )


__all__ = [
    "ETL_ISSUE_ID",
    "REASON_PRESCREEN_PASS",
    "REASON_SAMPLED_DRY_RUN",
    "REASON_SAMPLED_VALIDATED",
    "PromotionReport",
    "PromotionSummary",
    "build_formal_row",
    "load_admission_manifest",
    "promote_admitted_rows",
]
