"""NFM-4312 (BUG-32) — repoint Owen-provenance datasets onto amorphous UO2.

Production state (nucpot-prod-db, 2026-09-05): 96 of 114
``property_measurements`` rows sit on the "Unknown Material (canonical)"
sentinel.  88 of them come from Owen et al. 2023 "Diffusion in undoped
and Cr-doped amorphous UO2" — 83 on the main sentinel dataset (real
source row) plus 5 single-row scatter datasets whose ``data_sources``
rows carry the NFM-4088 UUID-title signature.  4 more Owen
activation-energy measurements are mis-attached to the *crystalline*
UO2 (Fluorite) material through 4 further UUID-titled sources.

This script moves all 10 datasets (92 measurements) onto the earliest
amorphous-UO2 fragment material, normalizes that row's identity fields
(``formula='UO2'``, ``crystal_structure='amorphous'``), and retires the
duplicate same-named fragment by moving its datasets and flipping
``is_active=False``.

Selection predicate (exact, no fuzzy matching):

    dataset.material_id IN (sentinel, crystalline UO2)
    AND (dataset.source_id == Owen real source
         OR source.title == "<Owen source's UUID string>")

Datasets from the properly-cited Owen source that sit on the
doping-distinct ``UO2-xat.%Cr`` fragment materials are deliberately NOT
moved — those are chemically distinct compositions.

Idempotency contract:
  * Dry-run (default) plans and prints, then rolls back — zero writes.
  * ``--apply`` executes the whole plan in ONE transaction.
  * Re-running after apply finds 0 pending moves and writes nothing.
  * A move that would violate ``uq_datasets_source_material`` (target
    already holds another dataset with the same source) is reported as
    a collision and aborts with exit code 2 in BOTH modes — dry-run
    and ``--apply`` — before any write.  Resolve collisions before
    proceeding.

Exit codes: 0 ok (dry-run or apply), 2 collisions detected,
1 configuration/validation error.

Runbook: RE executes against prod after NFM-4312's forward fix ships —
    uv run python scripts/backfill_nfm4312_material_association.py          # dry-run
    uv run python scripts/backfill_nfm4312_material_association.py --apply
A non-zero dry-run exit means the collision guard fired; do not run
``--apply`` until the reported collisions are resolved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import uuid as uuid_module
from dataclasses import dataclass
from pathlib import Path

# Make nfm_db importable when invoked as a standalone script.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _REPO_ROOT / "apps" / "api" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import func, or_, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from nfm_db.database import get_session_factory  # noqa: E402
from nfm_db.models import Dataset, DataSource, Material, PropertyMeasurement  # noqa: E402

logger = logging.getLogger("backfill_nfm4312")

# --- Production identifiers (2026-09-05 snapshot; all overridable) ---------

DEFAULT_SENTINEL_MATERIAL_ID = "021036bf-d7cc-434c-8f91-a08030027b4a"
DEFAULT_UO2_MATERIAL_ID = "068dc946-9dd9-4a8d-bad0-9f24359b8b87"
DEFAULT_TARGET_MATERIAL_ID = "3d084165-a282-418d-84b0-88a7a95cf98c"
DEFAULT_DUPLICATE_MATERIAL_ID = "794120c1-ab6e-4f9c-8682-e0a40de81011"
DEFAULT_OWEN_SOURCE_ID = "9320cb50-eb65-4178-8d2e-c56aeb848b21"
# NFM-4088 signature: the buggy run wrote the Owen source's PK UUID into
# other sources' ``title`` columns.  Exact-match against that string.
DEFAULT_UUID_TITLE = DEFAULT_OWEN_SOURCE_ID

_UUID_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Plan types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedMove:
    """One dataset re-point + enough context for the audit log."""

    dataset_id: str
    dataset_title: str
    source_id: str
    source_title: str
    from_material_id: str
    from_material_name: str
    measurement_count: int


@dataclass(frozen=True)
class BackfillPlan:
    """Immutable description of everything ``--apply`` would do."""

    target_material_id: str
    target_name: str
    moves: tuple[PlannedMove, ...]
    duplicate_material_id: str | None
    duplicate_moves: tuple[PlannedMove, ...]
    collisions: tuple[str, ...]
    target_formula_before: str | None
    target_formula_after: str
    target_crystal_structure_after: str
    sentinel_residue_measurements: int
    target_measurements_after: int

    @property
    def moved_measurements(self) -> int:
        return sum(m.measurement_count for m in self.moves)

    def summary(self, apply_mode: bool) -> dict[str, object]:
        return {
            "apply": apply_mode,
            "moved_datasets": len(self.moves),
            "moved_measurements": self.moved_measurements,
            "duplicate_retired": self.duplicate_material_id is not None,
            "duplicate_datasets_moved": len(self.duplicate_moves),
            "collisions": len(self.collisions),
            "sentinel_residue_measurements": self.sentinel_residue_measurements,
            "target_measurements_after": self.target_measurements_after,
        }


# ---------------------------------------------------------------------------
# Plan construction (pure — selftest target)
# ---------------------------------------------------------------------------


def _validate_uuid(value: str, flag: str) -> None:
    try:
        if uuid_module.UUID(value) and _UUID_PATTERN.match(value):
            return
    except ValueError:
        pass
    raise ValueError(f"{flag} must be a canonical UUID, got {value!r}")


def _uid(value: str) -> uuid_module.UUID:
    """Cast a canonical UUID string for ORM binds (``Uuid`` column type)."""
    return uuid_module.UUID(value)


async def _select_moves(
    db: AsyncSession,
    *,
    from_material_ids: tuple[str, ...],
    owen_source_id: str,
    uuid_title: str,
) -> tuple[PlannedMove, ...]:
    stmt = (
        select(
            Dataset.id,
            Dataset.title,
            DataSource.id,
            DataSource.title,
            Material.id,
            Material.name,
            func.count(PropertyMeasurement.id),
        )
        .join(DataSource, DataSource.id == Dataset.source_id)
        .join(Material, Material.id == Dataset.material_id)
        .outerjoin(PropertyMeasurement, PropertyMeasurement.dataset_id == Dataset.id)
        .where(
            Dataset.material_id.in_([_uid(m) for m in from_material_ids]),
            or_(
                DataSource.id == _uid(owen_source_id),
                DataSource.title == uuid_title,
            ),
        )
        .group_by(
            Dataset.id,
            Dataset.title,
            DataSource.id,
            DataSource.title,
            Material.id,
            Material.name,
        )
        .order_by(Material.name, Dataset.created_at)
    )
    rows = (await db.execute(stmt)).all()
    return tuple(
        PlannedMove(
            dataset_id=str(ds_id),
            dataset_title=ds_title,
            source_id=str(src_id),
            source_title=src_title,
            from_material_id=str(mat_id),
            from_material_name=mat_name,
            measurement_count=n_meas,
        )
        for ds_id, ds_title, src_id, src_title, mat_id, mat_name, n_meas in rows
    )


async def _all_datasets_of(db: AsyncSession, material_id: str) -> tuple[PlannedMove, ...]:
    """Every dataset currently on ``material_id`` (used for the dup merge)."""
    stmt = (
        select(
            Dataset.id,
            Dataset.title,
            DataSource.id,
            DataSource.title,
            Material.id,
            Material.name,
            func.count(PropertyMeasurement.id),
        )
        .join(DataSource, DataSource.id == Dataset.source_id)
        .join(Material, Material.id == Dataset.material_id)
        .outerjoin(PropertyMeasurement, PropertyMeasurement.dataset_id == Dataset.id)
        .where(Dataset.material_id == _uid(material_id))
        .group_by(
            Dataset.id,
            Dataset.title,
            DataSource.id,
            DataSource.title,
            Material.id,
            Material.name,
        )
        .order_by(Dataset.created_at)
    )
    rows = (await db.execute(stmt)).all()
    return tuple(
        PlannedMove(
            dataset_id=str(ds_id),
            dataset_title=ds_title,
            source_id=str(src_id),
            source_title=src_title,
            from_material_id=str(mat_id),
            from_material_name=mat_name,
            measurement_count=n_meas,
        )
        for ds_id, ds_title, src_id, src_title, mat_id, mat_name, n_meas in rows
    )


async def _count_measurements(
    db: AsyncSession, material_id: str, exclude_dataset_ids: frozenset[str]
) -> int:
    stmt = (
        select(func.count(PropertyMeasurement.id))
        .join(Dataset, Dataset.id == PropertyMeasurement.dataset_id)
        .where(Dataset.material_id == _uid(material_id))
    )
    if exclude_dataset_ids:
        stmt = stmt.where(Dataset.id.not_in([_uid(x) for x in exclude_dataset_ids]))
    return (await db.execute(stmt)).scalar_one()


async def build_plan(
    db: AsyncSession,
    *,
    sentinel_material_id: str,
    uo2_material_id: str,
    target_material_id: str,
    owen_source_id: str,
    uuid_title: str,
    duplicate_material_id: str | None,
    target_formula: str,
    target_crystal_structure: str,
) -> BackfillPlan:
    """Compute the full backfill plan without writing anything.

    Raises ``ValueError`` on invalid configuration (non-UUID arguments,
    missing target material, non-canonical uuid_title).
    """
    for flag, value in (
        ("--sentinel-material-id", sentinel_material_id),
        ("--uo2-material-id", uo2_material_id),
        ("--target-material-id", target_material_id),
        ("--owen-source-id", owen_source_id),
    ):
        _validate_uuid(value, flag)
    if not _UUID_PATTERN.match(uuid_title):
        raise ValueError(
            f"--uuid-title must be a canonical UUID string (NFM-4088 signature), got {uuid_title!r}"
        )
    ids = {sentinel_material_id, uo2_material_id, target_material_id}
    if len(ids) != 3:
        raise ValueError("sentinel / uo2 / target material ids must be distinct")
    if duplicate_material_id is not None:
        _validate_uuid(duplicate_material_id, "--duplicate-material-id")
        if duplicate_material_id in ids:
            raise ValueError("--duplicate-material-id must differ from the other three")

    target = await db.get(Material, _uid(target_material_id))
    if target is None:
        raise ValueError(f"target material {target_material_id} not found")

    moves = await _select_moves(
        db,
        from_material_ids=(sentinel_material_id, uo2_material_id),
        owen_source_id=owen_source_id,
        uuid_title=uuid_title,
    )
    duplicate_moves: tuple[PlannedMove, ...] = ()
    if duplicate_material_id is not None:
        dup = await db.get(Material, _uid(duplicate_material_id))
        if dup is not None:
            duplicate_moves = await _all_datasets_of(db, duplicate_material_id)

    # Collision guard for uq_datasets_source_material: the target must not
    # already hold a *different* dataset with the same source as any mover.
    target_source_stmt = select(Dataset.source_id).where(
        Dataset.material_id == _uid(target_material_id)
    )
    target_sources = {str(s) for s in (await db.execute(target_source_stmt)).scalars().all()}
    all_movers = moves + duplicate_moves
    collisions: list[str] = []
    seen: set[str] = set()
    for move in all_movers:
        if move.source_id in target_sources or move.source_id in seen:
            collisions.append(move.dataset_id)
        seen.add(move.source_id)

    moving_dataset_ids = frozenset(m.dataset_id for m in moves)
    residue = await _count_measurements(db, sentinel_material_id, moving_dataset_ids)
    target_now = await _count_measurements(db, target_material_id, frozenset())

    return BackfillPlan(
        target_material_id=target_material_id,
        target_name=target.name,
        moves=moves,
        duplicate_material_id=duplicate_material_id,
        duplicate_moves=duplicate_moves,
        collisions=tuple(collisions),
        target_formula_before=target.formula,
        target_formula_after=target_formula,
        target_crystal_structure_after=target_crystal_structure,
        sentinel_residue_measurements=residue,
        target_measurements_after=target_now + sum(m.measurement_count for m in all_movers),
    )


# ---------------------------------------------------------------------------
# Apply + report
# ---------------------------------------------------------------------------


async def apply_plan(db: AsyncSession, plan: BackfillPlan) -> None:
    """Execute ``plan`` on the given session (caller commits)."""
    target = await db.get(Material, _uid(plan.target_material_id))
    if target is None:  # pragma: no cover - build_plan guarantees existence
        raise ValueError("target material vanished between plan and apply")

    target_uid = _uid(plan.target_material_id)
    for move in plan.moves + plan.duplicate_moves:
        dataset = await db.get(Dataset, _uid(move.dataset_id))
        if dataset is None:  # pragma: no cover
            raise ValueError(f"dataset {move.dataset_id} vanished between plan and apply")
        dataset.material_id = target_uid

    target.formula = plan.target_formula_after
    target.crystal_structure = plan.target_crystal_structure_after

    if plan.duplicate_material_id is not None:
        dup = await db.get(Material, _uid(plan.duplicate_material_id))
        if dup is not None:
            dup.is_active = False


def format_report(plan: BackfillPlan) -> str:
    lines = [
        f"NFM-4312 backfill plan — target {plan.target_material_id} ({plan.target_name!r})",
        f"formula: {plan.target_formula_before!r} -> {plan.target_formula_after!r}, "
        f"crystal_structure -> {plan.target_crystal_structure_after!r}",
        "",
    ]
    for move in plan.moves:
        lines.append(
            f"  move {move.dataset_id}  n={move.measurement_count:<3} "
            f"from {move.from_material_name!r}  "
            f"source={move.source_title[:48]!r}"
        )
    for move in plan.duplicate_moves:
        lines.append(f"  merge {move.dataset_id} (duplicate fragment)  n={move.measurement_count}")
    lines += [
        "",
        f"datasets moved: {len(plan.moves)} ({plan.moved_measurements} measurements)",
        f"duplicate fragment retired: {plan.duplicate_material_id}",
        f"sentinel residue after: {plan.sentinel_residue_measurements}",
        f"target measurements after: {plan.target_measurements_after}",
        f"collisions: {len(plan.collisions)}"
        + (f" -> {plan.collisions}" if plan.collisions else ""),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NFM-4312: repoint Owen-provenance datasets onto amorphous UO2."
    )
    parser.add_argument("--sentinel-material-id", default=DEFAULT_SENTINEL_MATERIAL_ID)
    parser.add_argument("--uo2-material-id", default=DEFAULT_UO2_MATERIAL_ID)
    parser.add_argument("--target-material-id", default=DEFAULT_TARGET_MATERIAL_ID)
    parser.add_argument(
        "--duplicate-material-id",
        default=DEFAULT_DUPLICATE_MATERIAL_ID,
        help="fragment merged into the target then retired; '' to skip",
    )
    parser.add_argument("--owen-source-id", default=DEFAULT_OWEN_SOURCE_ID)
    parser.add_argument("--uuid-title", default=DEFAULT_UUID_TITLE)
    parser.add_argument("--target-formula", default="UO2")
    parser.add_argument("--target-crystal-structure", default="amorphous")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="execute the plan (default: dry-run, zero writes)",
    )
    args = parser.parse_args(argv)
    if args.duplicate_material_id == "":
        args.duplicate_material_id = None
    return args


async def _main(args: argparse.Namespace) -> int:
    async with get_session_factory()() as session:
        try:
            plan = await build_plan(
                session,
                sentinel_material_id=args.sentinel_material_id,
                uo2_material_id=args.uo2_material_id,
                target_material_id=args.target_material_id,
                owen_source_id=args.owen_source_id,
                uuid_title=args.uuid_title,
                duplicate_material_id=args.duplicate_material_id,
                target_formula=args.target_formula,
                target_crystal_structure=args.target_crystal_structure,
            )
        except ValueError as exc:
            logger.error("invalid backfill configuration: %s", exc)
            await session.rollback()
            return 1

        print(format_report(plan))
        print(json.dumps(plan.summary(args.apply)))

        if plan.collisions:
            logger.error(
                "refusing to apply: %d dataset move(s) would violate uq_datasets_source_material",
                len(plan.collisions),
            )
            await session.rollback()
            return 2

        if not args.apply:
            await session.rollback()
            print("DRY RUN — no changes written; pass --apply to execute")
            return 0

        await apply_plan(session, plan)
        await session.commit()
        print("APPLIED — changes committed")
        return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
