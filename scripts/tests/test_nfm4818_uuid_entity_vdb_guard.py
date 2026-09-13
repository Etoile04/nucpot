"""Tests for scripts/nfm-4818-assert-no-uuid-entities.sql (NFM-4818 AC4).

The pre-#1335 ingest bug serialized dedup-edge endpoints touching
matched ``kg_nodes`` as bare UUID strings, and the extraction LLM minted
UUID-named garbage entities in the LightRAG entity vdb (33 rows
observed 2026-09-12, 17 surviving at NFM-4736 close).  PR #1335 fixed
the source (``BuildResult.node_labels`` covers created+matched nodes)
and PR #1342 added the serialize skip+warn + ``_create_edge`` back-fill;
the 09-12 re-extract campaign swept the residual rows to 0.

These tests pin the regression guard that fails when a UUID-pattern
entity name re-enters the vdb:

  - the SQL's embedded UUID pattern is byte-identical to the canonical
    ``_UUID_TITLE_PATTERN`` from the write-path guard (NFM-4088) — the
    same anchored 36-char regex that counted the 33/17 junk rows
  - the pattern is anchored and rejects near-miss names (``ent-<hash>``
    ids, material names, UUID substrings)
  - the assertion covers every entity-name surface in the lightrag
    schema (vdb, KV chunk index, relation endpoints, graph-unit JSONB)
  - it fails loudly: ``RAISE EXCEPTION`` under ``ON_ERROR_STOP`` so the
    sanctioned gate exits non-zero
  - it is strictly read-only — no DML/DDL can hide in the assertion

The SQL is executed for real through the sanctioned gate at
``/usr/local/lib/nfm-g2/run-sql.sh`` (ADR-013 G2 / NFM-4270); these
unit tests pin its shape so a careless edit cannot silently defang it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "scripts" / "nfm-4818-assert-no-uuid-entities.sql"
MAPPER_PATH = (
    REPO_ROOT
    / "apps"
    / "api"
    / "src"
    / "nfm_db"
    / "services"
    / "extraction_to_db_mapper_lookups.py"
)

# Entity-name surfaces the guard must cover (table, name-bearing column).
EXPECTED_SURFACES: tuple[tuple[str, str], ...] = (
    ("lightrag_vdb_entity_nomic_embed_text_768d", "entity_name"),
    ("lightrag_entity_chunks", "id"),
    ("lightrag_vdb_relation_nomic_embed_text_768d", "source_id"),
    ("lightrag_vdb_relation_nomic_embed_text_768d", "target_id"),
    ("lightrag_full_entities", "entity_names"),
    ("lightrag_full_relations", "relation_pairs"),
)


def _surface_matcher(table: str, column: str) -> re.Pattern[str]:
    """Semantic FROM/WHERE pairing that pins one guarded surface.

    A bare table+column substring scan passes even when the column is
    never tested (e.g. ``id`` matching inside ``source_id``), so each
    surface is pinned as a query over the table whose name-bearing
    column reaches a regex ``~`` operator — directly for scalar
    columns, via ``jsonb_array_elements_text`` unwrapping for the
    graph-unit array columns.  ``[^;]*`` keeps the pairing inside a
    single statement.

    ``relation_pairs`` gets its own branch because it stores
    ``[src, tgt]`` PAIRS — an array of arrays (code-review NFM-4818
    R1).  A single-level ``jsonb_array_elements_text(relation_pairs)``
    yields the *serialized sub-array text* (``'["src","tgt"]'``),
    which an anchored ``^…$`` pattern can never match: the surface
    would be silently inert.  The matcher therefore demands the
    two-level unnest — ``jsonb_array_elements`` over the pairs outer,
    ``jsonb_array_elements_text`` over each pair's endpoint names
    inner — and, because ``jsonb_array_elements\\(`` cannot match the
    longer ``jsonb_array_elements_text(`` token, it structurally
    rejects the flat-array shape for this column.
    """
    if column == "relation_pairs":
        return re.compile(
            rf"FROM\s+{table}\b[^;]*"
            rf"jsonb_array_elements\(\s*{column}\s*\)[^;]*"
            rf"jsonb_array_elements_text\(\s*\w+\.\w+\s*\)[^;]*"
            rf"WHERE\s+\w+\.\w+\s*~",
            re.IGNORECASE,
        )
    if column == "entity_names":
        return re.compile(
            rf"FROM\s+{table}\b[^;]*"
            rf"jsonb_array_elements_text\(\s*{column}\s*\)[^;]*"
            rf"WHERE\s+\w+\.\w+\s*~",
            re.IGNORECASE,
        )
    return re.compile(
        rf"FROM\s+{table}\b[^;]*?WHERE[^;]*?\b{column}\b\s*~",
        re.IGNORECASE,
    )


# AC2 stability metrics the assertion must report alongside the verdict.
EXPECTED_METRICS: tuple[str, ...] = (
    "vdb_entity_total",
    "vdb_chunks_total",
    "docs_processed",
)

# The full sanctioned-gate command the header must document verbatim
# (ADR-013 G2 / NFM-4270) — the operational contract for running the
# assertion against prod.
_SANCTIONED_INVOCATION = "sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-sql.sh"

# Destructive verbs banned from the (comment-stripped) assertion body.
# Word-boundary anchored so column names like ``update_time`` pass.
_BANNED_VERBS = re.compile(
    r"\b(delete|insert|update|drop|truncate|alter|create|grant|revoke)\b",
    re.IGNORECASE,
)

# psql meta-command mutation vectors: shell escape, server-side
# PROGRAM execution, and table copies — the non-SQL write path in a
# file piped to psql.
_BANNED_META = re.compile(
    r"(\\!|\\copy\b|FROM\s+PROGRAM)",
    re.IGNORECASE,
)

# Single-quoted SQL string literals shaped like the canonical pattern
# (^…$ regex over hex/dash/brace/bracket chars) — pulls every embedded
# pattern out of the assertion file.
_SQL_REGEX_LITERAL = re.compile(r"'(\^[0-9a-fA-F\-\{\}\[\]\$]+\$)'")

# Canonical pattern source: re.compile(<adjacent string literals>) —
# the literals concatenate into one pattern string.
_CANONICAL_ASSIGN = re.compile(
    r"_UUID_TITLE_PATTERN[^=]*=\s*re\.compile\(\s*((?:r?\"[^\"]*\"\s*)+)\)",
    re.S,
)
_STRING_LITERAL = re.compile(r'r?"([^"]*)"')


def _canonical_pattern() -> str:
    """Extract ``_UUID_TITLE_PATTERN``'s source from the mapper module.

    Imported by source-parse rather than ``import`` so this test has no
    dependency on the api package's import graph (sqlalchemy & co) —
    scripts/tests run under a bare pytest in CI (batch1-ci.yml).
    """
    src = MAPPER_PATH.read_text(encoding="utf-8")
    match = _CANONICAL_ASSIGN.search(src)
    assert match is not None, (
        f"cannot find _UUID_TITLE_PATTERN assignment in {MAPPER_PATH} — "
        "the canonical UUID definition moved; update this test and the "
        "assertion SQL together"
    )
    return "".join(_STRING_LITERAL.findall(match.group(1)))


def _sql_text() -> str:
    assert SQL_PATH.exists(), f"missing {SQL_PATH} — NFM-4818 AC4 requires the SQL assertion"
    return SQL_PATH.read_text(encoding="utf-8")


def _sql_code_lines() -> list[str]:
    """Non-comment, non-blank lines of the assertion (psql meta-commands kept)."""
    lines = []
    for line in _sql_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(line)
    return lines


class TestPatternParity:
    """The guard must reuse the exact matching that counted the junk rows."""

    def test_sql_patterns_equal_canonical(self) -> None:
        sql = _sql_text()
        embedded = _SQL_REGEX_LITERAL.findall(sql)
        assert embedded, "assertion SQL embeds no UUID pattern literal"
        canonical = _canonical_pattern()
        assert set(embedded) == {canonical}, (
            "every embedded pattern must be byte-identical to the "
            f"canonical _UUID_TITLE_PATTERN {canonical!r}; found {sorted(set(embedded))!r}"
        )

    def test_canonical_pattern_semantics(self) -> None:
        pattern = re.compile(_canonical_pattern())
        # The actual dedup-edge endpoint shape observed on prod
        # (NFM-4736, data_source:8e60e867-…).
        assert pattern.match("8e60e867-a139-47a8-ae18-470be5eff911")
        assert pattern.match("8E60E867-A139-47A8-AE18-470BE5EFF911")
        # Legitimate entity names must never match.
        assert not pattern.match("UO2")
        assert not pattern.match("UO2-Cr")
        assert not pattern.match("Zr-4")
        assert not pattern.match("U-Pu-Zr合金")
        assert not pattern.match("MATPRO模型模拟方法")
        # Anchored both ends: a UUID embedded in a longer name is not
        # a bare-UUID endpoint.
        assert not pattern.match("x8e60e867-a139-47a8-ae18-470be5eff911")
        assert not pattern.match("8e60e867-a139-47a8-ae18-470be5eff911x")
        assert not pattern.match("")
        # Post-#1342 entity ids are ent-<hash>, not UUIDs — must pass.
        assert not pattern.match("ent-3f2b81c0a9d24e6b")


class TestGuardShape:
    """The assertion must fail loudly and cover every entity-name surface."""

    def test_fail_loud_under_on_error_stop(self) -> None:
        code = "\n".join(_sql_code_lines())
        on_error_stop = re.search(r"\\set\s+ON_ERROR_STOP\s+on\b", code)
        assert on_error_stop is not None, (
            "\\set ON_ERROR_STOP on must precede the assertion — "
            "without it a RAISE EXCEPTION still lets psql exit 0"
        )
        do_block = re.search(r"DO\s+\$\$(.*?)\$\$;", code, re.S)
        assert do_block is not None, "verdict must live in a DO block"
        assert on_error_stop.start() < do_block.start(), (
            "ON_ERROR_STOP must be set before the DO block — psql "
            "processes meta-commands sequentially"
        )
        body = do_block.group(1)
        assert re.search(r"RAISE\s+EXCEPTION", body), (
            "verdict must be a RAISE EXCEPTION — NOTICE-only guards "
            "exit 0 even when UUID rows re-enter"
        )
        assert "NFM-4818" in body, "exception text must cite NFM-4818 for grep"

    def test_covers_all_entity_name_surfaces(self) -> None:
        code = "\n".join(_sql_code_lines())
        missing = [
            f"{table}.{column}"
            for table, column in EXPECTED_SURFACES
            if not _surface_matcher(table, column).search(code)
        ]
        assert not missing, (
            f"assertion does not test {missing} against the UUID "
            "pattern — a UUID name re-entering through that surface "
            "would go undetected"
        )

    def test_relation_pairs_requires_two_level_unnest(self) -> None:
        """relation_pairs is an array of [src, tgt] arrays (NFM-4818 R1).

        A single-level ``jsonb_array_elements_text(relation_pairs)``
        yields the serialized sub-array text (``'["src","tgt"]'``), so
        an anchored ``^…$`` UUID pattern can never match — surface 5
        would be silently inert (CR R1 HIGH defect).  The matcher must
        accept the shipped two-level unnest AND reject the flat-array
        shape that passed review-blind at 344f7ea7c.
        """
        code = "\n".join(_sql_code_lines())
        matcher = _surface_matcher("lightrag_full_relations", "relation_pairs")
        assert matcher.search(code), (
            "full_relations surface must unnest two levels: "
            "jsonb_array_elements(relation_pairs) outer, then "
            "jsonb_array_elements_text(pair) over each pair's endpoint "
            "names, inner name reaching the ~ regex"
        )
        # The exact inert shape shipped at 344f7ea7c: flat
        # elements_text over an array-of-arrays.  Must NOT satisfy the
        # relation_pairs matcher.
        flat_shape = (
            "SELECT count(*) INTO v FROM lightrag_full_relations "
            "WHERE EXISTS (SELECT 1 FROM "
            "jsonb_array_elements_text(relation_pairs) AS p(pair) "
            "WHERE p.pair ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
            "[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');"
        )
        assert not matcher.search(flat_shape), (
            "relation_pairs matcher accepts the single-level "
            "elements_text shape — that shape is semantically inert "
            "for an array of arrays and must be rejected"
        )

    def test_reports_stability_metrics(self) -> None:
        code = "\n".join(_sql_code_lines())
        missing = [
            m
            for m in EXPECTED_METRICS
            if not re.search(
                rf"'{m}'(?:\s+AS\s+\w+)?\s*,\s*count\(\*\)"
                rf"(?:\s+AS\s+\w+)?\s+FROM\s+lightrag_\w+",
                code,
            )
        ]
        assert not missing, (
            f"assertion must report AC2 stability metrics {missing} "
            "as row counts alongside the verdict so gate output "
            "doubles as evidence"
        )

    def test_strictly_read_only(self) -> None:
        code = "\n".join(_sql_code_lines())
        hit = _BANNED_VERBS.search(code) or _BANNED_META.search(code)
        assert hit is None, (
            f"assertion must stay read-only; found {hit.group(0)!r} — "
            "prod data mutation belongs to a separately reviewed tool"
        )

    def test_documents_sanctioned_invocation(self) -> None:
        header = _sql_text()
        assert _SANCTIONED_INVOCATION in header, (
            "header must document the full sanctioned gate invocation "
            f"({_SANCTIONED_INVOCATION}) — not just an ambiguous "
            "script-name mention"
        )


class TestGuardSum:
    """Verdict arithmetic: any single positive surface count fails."""

    def test_exception_condition_sums_all_counters(self) -> None:
        code = "\n".join(_sql_code_lines())
        # Every counter variable declared in the DO block must appear in
        # the RAISE EXCEPTION's IF condition — a forgotten summand is a
        # silent blind spot.
        declared = re.findall(r"(v_[a-z_]+)\s+bigint", code)
        assert declared, "DO block declares no counters"
        if_block = re.search(r"IF (.+?) THEN\s*\n\s*RAISE EXCEPTION", code, re.S)
        assert if_block is not None, "no IF … THEN RAISE EXCEPTION verdict"
        missing = [v for v in declared if v not in if_block.group(1)]
        assert not missing, (
            f"counters {missing} not part of the failure condition — "
            "their surfaces could regress without failing the guard"
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
