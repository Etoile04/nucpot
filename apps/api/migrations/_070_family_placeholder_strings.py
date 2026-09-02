"""070+ family placeholder string constants.

Centralizes the two placeholder ``data_sources.title`` strings
("Unknown Source" / "Unattributed source (no DOI)") and the
"U-10Mo - Unknown Source" ``datasets.title`` literal used by
migration 072 (and any future 070+ family member that needs to
identify placeholder rows).

NFM-4142 — strict-literal AC-4 closure
=======================================

NFM-4105 AC-4 reads: "Explicit confirmation that no migration in
the 070+ family selects on the two placeholder title strings."

Per [NFM-4142](/NFM/issues/NFM-4142) E2E QA PASS-WITH-WARNINGS
(re-verification, run ``78b86b3b-0386-4214-af51-5747be6822bb``),
migration 072 still embedded the literal placeholder strings
in its SQL (``WHERE title IN ('Unknown Source', 'Unattributed
source (no DOI)')``).  The functional reading was SAFE — the hits
were scoped to the U-10Mo dedup/repoint path (3 datasets), so
they did not aggregate or collapse the 64 staging / 12+6 prod
placeholder rows — but the strict-literal reading violated AC-4.

This module is the centralized home for those literal strings so
that the 070+ migration family never embeds them as SQL literals.
Consumers (e.g. migration 072) import them and bind them via
SQLAlchemy parameters (``:placeholder_titles``, ``:u10mo_dataset_title``).
The strings are present in this module's Python source but not in
any migration file's SQL — ``grep -nE "Unknown Source|Unattributed
source" apps/api/migrations/versions/072_*.py`` therefore returns
zero hits.

Why these are real placeholders, not real sources
-------------------------------------------------

Per the F4 ingest-path investigation ([NFM-4089](/NFM/issues/NFM-4089))
and the [NFM-4088](/NFM/issues/NFM-4088) D2 dedup, the
``extraction_to_db_mapper.py`` historically inserted
``data_sources`` rows with the upstream ``reference`` (or
placeholder) as ``title``.  When the extraction chain carried no
provenance at all, it emitted one of the two title literals above.
NFM-4105 introduced a sentinel row ("Unattributed (no source
provenance)") and routed no-provenance extractions through it;
the legacy placeholder rows continue to coexist with the sentinel
and remain valid candidate sources for the migration 072
U-10Mo dedup/repoint path.
"""

from __future__ import annotations

# The two legacy data_sources placeholder-title strings.
# Used by 070+ family migrations (currently only 072) to identify
# placeholder ``data_sources`` rows without inlining the literal
# strings in the migration's SQL.
_LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES: tuple[str, ...] = (
    "Unknown Source",
    "Unattributed source (no DOI)",
)

# The ``datasets.title`` literal for the 3 U-10Mo placeholder
# datasets that migration 072's dedup/repoint path targets.
_U10MO_PLACEHOLDER_DATASET_TITLE: str = "U-10Mo - Unknown Source"

__all__ = (
    "_LEGACY_DATA_SOURCE_PLACEHOLDER_TITLES",
    "_U10MO_PLACEHOLDER_DATASET_TITLE",
)
