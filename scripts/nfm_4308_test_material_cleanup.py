#!/usr/bin/env python3
"""NFM-4308 ⑤ — production test-material cleanup tool (BUG-36 ⑤).

Finds materials whose name matches the test-data pattern
(``Test``, ``E2E-Test-*`` …) and, with ``--execute``, deletes them.
Rows that carry real dependent data (datasets / DFT calculations) are
NEVER auto-deleted — they are listed for manual review instead.

Usage:
    # Dry-run (default) — lists what would be deleted, deletes nothing:
    python scripts/nfm_4308_test_material_cleanup.py \
        --database-url "$PROD_DATABASE_URL"

    # Execute the cleanup (aliases/compositions cascade with the row):
    python scripts/nfm_4308_test_material_cleanup.py \
        --database-url "$PROD_DATABASE_URL" --execute

Output: a Markdown table on stdout suitable for pasting into a
Paperclip comment. Exits non-zero if --execute is requested without a
database URL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make nfm_db importable when run from a fresh checkout.
_SRC = Path(__file__).resolve().parents[1] / "apps" / "api" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nfm_db.models import (  # noqa: E402
    ConflictRecord,
    Dataset,
    DFTCalculation,
    EntityMergeLog,
    Material,
    MaterialAlias,
    MaterialComposition,
)
from nfm_db.schemas.material import TEST_MATERIAL_NAME_PATTERN  # noqa: E402


@dataclass(frozen=True)
class TestMaterialCandidate:
    """One test-named material plus the data that hangs off it."""

    id: uuid.UUID
    name: str
    dataset_count: int
    dft_count: int
    merge_log_count: int
    conflict_count: int

    @property
    def deletable(self) -> bool:
        """Safe to delete without manual review (no dependent real data)."""
        return (
            self.dataset_count == 0
            and self.dft_count == 0
            and self.merge_log_count == 0
            and self.conflict_count == 0
        )


@dataclass(frozen=True)
class CleanupReport:
    """Outcome summary for one run."""

    deleted: list[str]
    skipped: list[str]

    @property
    def deleted_count(self) -> int:
        return len(self.deleted)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


async def find_test_materials(db: AsyncSession) -> list[TestMaterialCandidate]:
    """Return all test-named materials with their dependent-data counts."""
    rows = (await db.execute(select(Material.id, Material.name))).all()
    candidates: list[TestMaterialCandidate] = []
    for material_id, name in rows:
        if not TEST_MATERIAL_NAME_PATTERN.match(name.strip()):
            continue
        dataset_count = (
            await db.execute(
                select(func.count()).select_from(Dataset).where(Dataset.material_id == material_id)
            )
        ).scalar_one()
        dft_count = (
            await db.execute(
                select(func.count())
                .select_from(DFTCalculation)
                .where(DFTCalculation.material_id == material_id)
            )
        ).scalar_one()
        merge_log_count = (
            await db.execute(
                select(func.count())
                .select_from(EntityMergeLog)
                .where(
                    (EntityMergeLog.canonical_id == material_id)
                    | (EntityMergeLog.merged_id == material_id)
                )
            )
        ).scalar_one()
        conflict_count = (
            await db.execute(
                select(func.count())
                .select_from(ConflictRecord)
                .where(ConflictRecord.material_id == material_id)
            )
        ).scalar_one()
        candidates.append(
            TestMaterialCandidate(
                id=material_id,
                name=name,
                dataset_count=dataset_count,
                dft_count=dft_count,
                merge_log_count=merge_log_count,
                conflict_count=conflict_count,
            )
        )
    candidates.sort(key=lambda c: c.name)
    return candidates


async def delete_test_materials(db: AsyncSession) -> CleanupReport:
    """Delete every deletable test-named material; skip rows with dependents.

    Aliases and compositions are removed explicitly (portable across
    backends regardless of FK pragma state); datasets / DFT calculations
    mark the row for manual review instead of being cascaded away.
    """
    deleted: list[str] = []
    skipped: list[str] = []
    for candidate in await find_test_materials(db):
        if not candidate.deletable:
            skipped.append(candidate.name)
            continue
        material_id = candidate.id
        await db.execute(
            delete(MaterialAlias).where(MaterialAlias.material_id == material_id)
        )
        await db.execute(
            delete(MaterialComposition).where(
                MaterialComposition.material_id == material_id
            )
        )
        await db.execute(delete(Material).where(Material.id == material_id))
        deleted.append(candidate.name)
    await db.commit()
    return CleanupReport(deleted=deleted, skipped=skipped)


def _render_table(
    candidates: list[TestMaterialCandidate], report: CleanupReport | None
) -> str:
    lines = [
        "| 材料名 | id | datasets | dft | merges | conflicts | 处置 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    disposition: dict[str, str] = {name: "已删除" for name in (report.deleted if report else [])}
    disposition.update({name: "跳过(含真实数据,人工复核)" for name in (report.skipped if report else [])})
    for c in candidates:
        action = disposition.get(c.name, "待删除(dry-run)")
        lines.append(
            f"| {c.name} | `{c.id!s}` | {c.dataset_count} | {c.dft_count} "
            f"| {c.merge_log_count} | {c.conflict_count} | {action} |"
        )
    if not candidates:
        lines.append("| (无匹配测试数据) | - | - | - | - | - | - |")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    engine = create_async_engine(args.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        candidates = await find_test_materials(db)
        report: CleanupReport | None = None
        if args.execute:
            report = await delete_test_materials(db)
    await engine.dispose()

    print(_render_table(candidates, report))
    if args.execute and report is not None:
        print(
            f"\n执行结果: 删除 {report.deleted_count} 条, 跳过 {report.skipped_count} 条。"
        )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Async SQLAlchemy URL (overrides DATABASE_URL)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete (default is a dry-run that deletes nothing)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args: Any = _parse_args(argv)
    if not args.database_url:
        print("error: --database-url or DATABASE_URL is required", file=sys.stderr)
        return 2
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
