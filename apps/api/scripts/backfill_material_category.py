#!/usr/bin/env python3
"""Backfill ``materials.category_id`` from formula / crystal_structure heuristics.

NFM-3916 (Tier 1C) — companion to migration
``065_seed_material_categories``.  The seed migration inserts the
canonical 8-row taxonomy; this script assigns each material row to one
of those taxonomy rows using deterministic, formula-driven rules.

Why a separate script (not part of the alembic migration)
----------------------------------------------------------
The mapping is a *policy* decision, not a schema change.  Future
re-classifications (e.g. when Nuclear Domain Expert reviews the
names) should be re-runnable without rewriting the schema, and the
backfill output must be auditable (which formula → which category)
per row.  Keeping the policy outside alembic also means a downgrade
of the seed migration does not drag the backfill with it — operators
can roll back either side independently.

Idempotency contract
--------------------
Running this script twice against the same dataset produces the same
``category_id`` for every row.  Concretely:

* Rows whose ``category_id`` already equals the computed target are
  left untouched (``UPDATE ... WHERE category_id IS DISTINCT FROM``).
* Rows that match no rule are kept at ``category_id = NULL`` — per
  ticket NFM-3916 ("匹配不上的行保持 ``category_id=NULL`` (不硬塞进
  ``other``, 避免制造假分类)").  This makes the script deterministic
  by construction: no rule touches a NULL input.

Rule precedence
---------------
Rules are evaluated in the order listed below; the first matching rule
wins.  This ordering reflects specificity: structural / compositional
overrides come before fallbacks.

1. ``crystal_structure == 'Fluorite'``  → ``oxide_fuel``  (covers
   ``cubic_Fluorite`` style ceramic fuels, e.g. ``CeO2``, ``ThO2``,
   ``UO2``; the 2 known Fluorite rows in the 131-row dataset map
   here).
2. Formula contains ``O`` (oxygen)  → ``oxide_fuel``  (UO2, PuO2,
   MOX, etc.).
3. Formula contains ``U`` / ``Pu`` / ``Th`` AND ``C``  →
   ``carbide_nitride_fuel``  (UC, (U,Pu)C).
4. Formula contains ``U`` / ``Pu`` / ``Th`` AND ``N``  →
   ``carbide_nitride_fuel``  (UN, (U,Pu)N).
5. Formula contains ``U`` / ``Pu`` / ``Th`` (no O/C/N)  →
   ``metallic_fuel``  (U-Zr, U-Pu-Zr, U-Mo binary / ternary
   intermetallics).
6. Formula is Zr-dominant (no U/Pu/Th)  → ``cladding_alloy``
   (Zircaloy, M5, E110, Zr-Nb).
7. Formula is Fe-dominant (no U/Pu/Th)  → ``structural_steel``
   (SS304, SS316, HT9, F82H, Eurofer97).
8. Formula contains any refractory metal ``W`` / ``Mo`` / ``Nb`` /
   ``Ta`` (no U/Pu/Th/Zr/Fe)  → ``refractory_metal``  (resolves
   ``Cr-Mo-V``, ``Pt-W``, and ``Nb-V`` to refractory; Nb-V lands
   here because Nb is a refractory base).  Note: a refractory
   symbol presence alone is sufficient — a binary alloy like
   ``Nb-V`` has 50% refractory and 50% non-refractory, so a
   strict-majority rule would miss it.  The earlier
   "non-ferrous cladding/structural" rules (6, 7) take precedence
   over rule 8 to keep ``Zr-Nb`` as a cladding alloy, not a
   refractory metal.
9. Formula has 2+ distinct metal elements (no U/Pu/Th)  →
   ``metallic_fuel``  (catches ``CuAu``, ``Ag-Pt``, binary/ternary
   intermetallics that are not refractory-dominated).
10. Otherwise  → ``NULL`` (leave unchanged; pure metals like Au, Cu
    that have no alloy context stay unclassified — the ticket
    explicitly forbids force-fitting).

Coverage expectation
--------------------
Production has 131 material rows (NFM-3916 triage).  Of those:

* ``crystal_structure = 'Fluorite'`` → 2 rows → ``oxide_fuel``
* ``Nb-V`` → 1 row → ``refractory_metal``
* ``Pt-W`` → 1 row → ``refractory_metal``
* ``Cr-Mo-V`` → 1 row → ``refractory_metal``
* ``CuAu`` → 1 row → ``metallic_fuel``
* ``Ag-Pt`` → 1 row → ``metallic_fuel``

Pure ``Au`` / ``Cu`` rows stay ``NULL`` under rule 10 (single
element, no fuel-context markers).  Coverage is therefore expected to
be in the 5-15% range, *not* 50%+.  The ticket explicitly requires
flagging the CPO if coverage is below 50% — that flag fires on the
production run, and the CPO decides whether to broaden the rule set
or proceed to Tier 1D with limited coverage.

Usage
-----
::

    cd apps/api && python scripts/backfill_material_category.py
    cd apps/api && python scripts/backfill_material_category.py --dry-run
    cd apps/api && python scripts/backfill_material_category.py --verbose
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Make ``nfm_db`` importable when run as a script (mirrors the pattern
# used in ``scripts/_generate_sample_candidates.py`` and
# ``scripts/doi_etl_admit.py``).
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from nfm_db.config import get_settings  # noqa: E402
from nfm_db.database import async_session_factory  # noqa: E402


logger = logging.getLogger("backfill_material_category")


# ---------------------------------------------------------------------------
# Element extraction
# ---------------------------------------------------------------------------
# Formula conventions observed in the 131-row production dataset:
#
# * Pure element:        "Au", "Cu"
# * Hyphen-separated:    "Nb-V", "Cr-Mo-V", "Pt-W", "Ag-Pt"
# * Concatenated (rare): "CuAu"  (Cu + Au)
#
# The parser normalises both styles to a set of element symbols.  We
# intentionally *do not* parse stoichiometric coefficients ("UO2" → we
# only care that O is present, not its count) — the rule predicates
# are all set-membership queries, never arithmetic.

# Element symbol: capital letter followed by 0-2 lowercase letters.
# The + in [A-Z][a-z]? is greedy on the trailing lowercase so "Mo"
# doesn't split into "M" + "o".
_ELEMENT_RE = re.compile(r"([A-Z][a-z]{0,2})")

# Refractory metals per the NFM-3916 ticket taxonomy.  We use the set
# directly rather than fetching it from the taxonomy so the rule
# engine remains pure-Python and unit-testable without a DB.
_REFRACTORY_METALS: frozenset[str] = frozenset({"W", "Mo", "Nb", "Ta"})

# Actinides that mark a material as a fuel.
_ACTINIDES: frozenset[str] = frozenset({"U", "Pu", "Th"})


def _formula_to_elements(formula: str | None) -> set[str]:
    """Return the set of element symbols referenced in ``formula``.

    Examples
    --------
    >>> sorted(_formula_to_elements("UO2"))
    ['O', 'U']
    >>> sorted(_formula_to_elements("Nb-V"))
    ['Nb', 'V']
    >>> sorted(_formula_to_elements("CuAu"))
    ['Au', 'Cu']
    >>> sorted(_formula_to_elements("Cr-Mo-V"))
    ['Cr', 'Mo', 'V']
    >>> _formula_to_elements(None)
    set()
    """
    if not formula:
        return set()
    # Hyphen-separated forms ("Nb-V") are split first so the regex
    # finds each segment's leading capital letter.  Concatenated
    # forms ("CuAu") still work because the regex anchors on
    # ``[A-Z]`` and will pick up "Cu" then "Au" sequentially.
    matches = _ELEMENT_RE.findall(formula.replace("-", ""))
    return {m for m in matches if m}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """Result of classifying a single material row.

    Attributes
    ----------
    target_slug
        The taxonomy slug this row should be assigned to, or ``None``
        if no rule matched (and the row stays ``category_id = NULL``).
    rule_id
        Identifier of the rule that fired (``"fluorite"``,
        ``"oxide_o"``, ..., ``"unmatched"``).  Surfaced in the audit
        log so a reviewer can trace each row's classification back
        to the rule that produced it.
    """

    target_slug: str | None
    rule_id: str


def classify(
    *,
    formula: str | None,
    crystal_structure: str | None,
    name: str | None = None,
) -> Classification:
    """Map a material row to a taxonomy slug.

    Pure function — no DB access, no side effects.  Tests can call it
    directly with synthetic formula/name/crystal_structure values
    without provisioning a database.

    Parameters
    ----------
    formula
        The ``materials.formula`` column.  May contain hyphens
        (e.g. ``"Nb-V"``) or concatenated symbols (e.g. ``"CuAu"``).
    crystal_structure
        The ``materials.crystal_structure`` column.  Only the
        ``"Fluorite"`` value is consulted for a structural override;
        other structures fall through to the formula-based rules.
    name
        Currently unused (reserved for future keyword-based
        classification, e.g. matching ``"Zircaloy-4"`` in the name
        even when the formula column is sparse).  Kept in the
        signature so the call sites mirror the schema and so the
        rule engine is forward-compatible with NFM-3916 follow-ups.

    Returns
    -------
    Classification
        See the dataclass docstring.
    """
    _ = name  # suppress unused-argument warning; reserved for future use

    elements = _formula_to_elements(formula)

    # Rule 1: structural override for fluorite-type ceramics.  Fires
    # before the O-containing rule so we don't accidentally catch
    # ``CeO2``-style non-fuel oxides (none in the current dataset,
    # but the explicit override is defensive against future imports).
    if crystal_structure == "Fluorite":
        return Classification(target_slug="oxide_fuel", rule_id="fluorite")

    # Rules 2-5: actinide-bearing ceramics and metals (the canonical
    # NFMD fuel taxonomy).
    has_actinide = bool(elements & _ACTINIDES)

    if "O" in elements:
        return Classification(target_slug="oxide_fuel", rule_id="oxide_o")

    if has_actinide and "C" in elements:
        return Classification(
            target_slug="carbide_nitride_fuel",
            rule_id="carbide_actinide",
        )

    if has_actinide and "N" in elements:
        return Classification(
            target_slug="carbide_nitride_fuel",
            rule_id="nitride_actinide",
        )

    if has_actinide:
        return Classification(
            target_slug="metallic_fuel",
            rule_id="metallic_actinide",
        )

    # Rules 6-7: cladding and structural alloys (no actinides).
    if "Zr" in elements:
        return Classification(
            target_slug="cladding_alloy",
            rule_id="cladding_zr",
        )

    if "Fe" in elements:
        return Classification(
            target_slug="structural_steel",
            rule_id="structural_fe",
        )

    # Rule 8: refractory-dominated alloy (no actinides / Zr / Fe).
    # Any refractory symbol (W, Mo, Nb, Ta) is sufficient — strict
    # majority is too restrictive for binary alloys like Nb-V
    # (50/50 refractory split).  Cladding (rule 6) and structural
    # steel (rule 7) already filtered the cases where Zr / Fe
    # dominates, so by the time we reach rule 8 the refractory
    # element is the most specific fuel-relevant marker.
    if elements & _REFRACTORY_METALS:
        return Classification(
            target_slug="refractory_metal",
            rule_id="refractory_any",
        )

    # Rule 9: any binary/ternary metal alloy (no actinides) is a
    # generic metallic-fuel candidate.  Catches CuAu, Ag-Pt, etc.
    if len(elements) >= 2:
        return Classification(
            target_slug="metallic_fuel",
            rule_id="metallic_binary",
        )

    # Rule 10: pure element or unrecognised formula → leave NULL.
    # Per ticket: do NOT force into "other" (would manufacture a
    # fake classification).
    return Classification(target_slug=None, rule_id="unmatched")


# ---------------------------------------------------------------------------
# Database runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageReport:
    """Summary of the backfill pass, suitable for logging and CI smoke tests."""

    total_rows: int
    matched_rows: int
    unmatched_rows: int
    already_correct_rows: int
    updated_rows: int
    by_rule: dict[str, int]
    by_slug: dict[str, int]

    @property
    def coverage_pct(self) -> float:
        """Matched rows as a percentage of total, 0.0-100.0."""
        if self.total_rows == 0:
            return 0.0
        return round(100.0 * self.matched_rows / self.total_rows, 2)

    def render(self) -> str:
        """Human-readable summary for the comment / log output."""
        lines = [
            "== backfill_material_category coverage report ==",
            f"total materials:     {self.total_rows}",
            f"matched (target):    {self.matched_rows}",
            f"unmatched (NULL):    {self.unmatched_rows}",
            f"already correct:     {self.already_correct_rows}",
            f"updated:             {self.updated_rows}",
            f"coverage:            {self.coverage_pct}%",
            "",
            "-- by rule --",
        ]
        for rule_id, count in sorted(self.by_rule.items()):
            lines.append(f"  {rule_id:<24s} {count}")
        lines.append("-- by assigned slug --")
        for slug, count in sorted(self.by_slug.items()):
            lines.append(f"  {slug:<24s} {count}")
        return "\n".join(lines)


async def _load_category_lookup(session: AsyncSession) -> dict[str, str]:
    """Build a slug → id map for the seeded material_categories.

    The backfill only writes category_ids that are actually present
    in the taxonomy table, so we materialise the full set up front.
    Missing taxonomy rows (e.g. operator never ran migration 065)
    produce an empty map and the backfill aborts with a clear error
    rather than silently writing orphaned category_ids.
    """
    rows = await session.execute(
        sa.text("SELECT slug, id FROM material_categories")
    )
    return {row[0]: str(row[1]) for row in rows.fetchall()}


async def _load_materials(session: AsyncSession) -> list[tuple[str, str | None, str | None, str | None]]:
    """Return ``[(id, formula, crystal_structure, current_category_id), ...]``.

    We deliberately read ``category_id`` so the idempotency guard
    (``WHERE category_id IS DISTINCT FROM ...``) can skip rows that
    are already correctly classified on a re-run.
    """
    result = await session.execute(
        sa.text(
            "SELECT id, formula, crystal_structure, category_id "
            "FROM materials"
        )
    )
    return [(str(r[0]), r[1], r[2], str(r[3]) if r[3] is not None else None)
            for r in result.fetchall()]


async def run_backfill(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> CoverageReport:
    """Execute the backfill inside an existing session and return a coverage report.

    Parameters
    ----------
    session
        Open async session.  The caller is responsible for
        committing; this function only emits ``UPDATE`` statements
        via ``session.execute``.  ``dry_run=True`` skips the
        ``UPDATE`` so a reviewer can preview the rule decisions
        without writing.
    dry_run
        When ``True``, classify every row and compute the report
        but emit no ``UPDATE``.  Useful for staging previews.
    verbose
        Emit per-row log lines (``logger.debug``).

    Returns
    -------
    CoverageReport
        See the dataclass docstring.
    """
    slug_to_id = await _load_category_lookup(session)
    if not slug_to_id:
        raise RuntimeError(
            "No material_categories rows found — has migration "
            "065_seed_material_categories been applied? Aborting "
            "to avoid orphaned category_id writes."
        )
    logger.info("loaded %d taxonomy rows from material_categories", len(slug_to_id))

    materials = await _load_materials(session)
    total = len(materials)
    logger.info("read %d material rows", total)

    matched = 0
    unmatched = 0
    already_correct = 0
    updated = 0
    by_rule: dict[str, int] = {}
    by_slug: dict[str, int] = {}

    for mat_id, formula, crystal_structure, current_category_id in materials:
        decision = classify(
            formula=formula,
            crystal_structure=crystal_structure,
        )

        by_rule[decision.rule_id] = by_rule.get(decision.rule_id, 0) + 1

        if decision.target_slug is None:
            unmatched += 1
            if verbose:
                logger.debug(
                    "row %s formula=%r crystal=%r → unmatched (NULL)",
                    mat_id, formula, crystal_structure,
                )
            continue

        matched += 1
        by_slug[decision.target_slug] = by_slug.get(decision.target_slug, 0) + 1
        target_id = slug_to_id.get(decision.target_slug)
        if target_id is None:
            # Defensive: classify() emits a slug the taxonomy does
            # not contain.  This should be impossible because
            # classify() only emits the eight canonical slugs, but
            # we fail loud rather than silently dropping the row.
            raise RuntimeError(
                f"classifier emitted slug {decision.target_slug!r} "
                "but material_categories has no row with that slug; "
                "check classify() and the seed migration for drift."
            )

        if current_category_id == target_id:
            already_correct += 1
            if verbose:
                logger.debug(
                    "row %s formula=%r crystal=%r → %s (already correct)",
                    mat_id, formula, crystal_structure, decision.target_slug,
                )
            continue

        if dry_run:
            logger.debug(
                "row %s formula=%r crystal=%r → %s (dry-run, no write)",
                mat_id, formula, crystal_structure, decision.target_slug,
            )
            continue

        # ``IS DISTINCT FROM`` treats NULL = NULL as equal, so a row
        # whose current category_id is NULL and whose target is also
        # NULL (rule 10 — but we already returned earlier for that)
        # would never reach this UPDATE.  Rows that reach this point
        # have ``current_category_id != target_id`` by construction.
        await session.execute(
            sa.text(
                "UPDATE materials "
                "SET category_id = CAST(:new_id AS UUID) "
                "WHERE id = CAST(:row_id AS UUID) "
                "AND category_id IS DISTINCT FROM CAST(:new_id AS UUID)"
            ),
            {"new_id": target_id, "row_id": mat_id},
        )
        updated += 1
        if verbose:
            logger.debug(
                "row %s formula=%r crystal=%r → %s (updated)",
                mat_id, formula, crystal_structure, decision.target_slug,
            )

    return CoverageReport(
        total_rows=total,
        matched_rows=matched,
        unmatched_rows=unmatched,
        already_correct_rows=already_correct,
        updated_rows=updated,
        by_rule=dict(sorted(by_rule.items())),
        by_slug=dict(sorted(by_slug.items())),
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backfill_material_category",
        description=(
            "Backfill materials.category_id from formula / crystal_structure "
            "heuristics (NFM-3916 Tier 1C). Idempotent: re-running against "
            "the same dataset produces the same category_id for every row."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify every row and print the coverage report, but emit no UPDATE.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Emit per-row DEBUG log lines showing each rule decision.",
    )
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    settings = get_settings()
    logger.info("connecting to %s", _redact_url(settings.database_url))
    async with async_session_factory() as session:
        report = await run_backfill(
            session, dry_run=args.dry_run, verbose=args.verbose,
        )
        if args.dry_run:
            # Roll back so a dry-run leaves the DB untouched even if
            # the runner emitted UPDATEs (it does not, but this is
            # belt-and-braces against future refactors that start
            # writing in dry-run mode).
            await session.rollback()
        else:
            await session.commit()

    print(report.render())
    return 0


def _redact_url(url: str) -> str:
    """Strip credentials from a database URL for log lines."""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        _, _, host_part = rest.partition("@")
        return f"{scheme}://***@{host_part}"
    return url


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
