"""Unit tests for the DOI ETL admission gate (NFM-3871 / C-I1).

Covers the three layers of the A+修正案 amendment:

1. Prescreen — every BLOCKED_* reason must fire on its known-bad
   pattern; PASS must fire on every real-look DOI from the curated
   ``seed_dois.json`` list (the 54-row nuclear materials seed).
2. Sample selection — deterministic across re-runs; >= ceil(N*rate)
   but never more than the cohort.
3. Manifest — a synthetic 170-row cohort matching the historical
   contamination mix (1.2 % placeholder DOI + 1 placeholder source +
   the rest real-looking DOIs) produces a summary with the right
   etl_ok / etl_blocked counts and every row has a manifest entry.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from nfm_db.services.doi_etl_admission import (
    DOI_REGEX,
    PLACEHOLDER_SOURCE_TOKENS,
    DOIMetadata,
    InMemoryBackend,
    PrescreenVerdict,
    StagingRow,
    ValidationVerdict,
    build_admission_manifest,
    cross_validate,
    manifest_to_jsonable,
    prescreen_doi,
    select_sample,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SEED_DOIS_PATH = _REPO_ROOT / "apps/api/src/nfm_db/data/seed_dois.json"


@pytest.fixture(scope="module")
def curated_real_dois() -> list[str]:
    """Real-looking DOIs from the nuclear-materials seed corpus.

    Every entry must pass prescreen_doi so we catch any future
    tightening of the regex that would block legitimate data.
    """
    payload = json.loads(_SEED_DOIS_PATH.read_text())
    return list(payload["dois"])


@pytest.fixture
def synthetic_170_cohort() -> list[StagingRow]:
    """170 rows approximating the historical contamination mix.

    Composition (deterministic for test assertions):
        * 156 rows with real-looking DOIs (from the curated seed list,
          cycled to fill the slot) and a non-placeholder ``source``
          attribution.
        * 4 rows with NULL source_doi (BLOCKED_NULL).
        * 3 rows with a format-broken DOI (BLOCKED_FORMAT).
        * 2 rows with placeholder registrant ``10.0000/...`` (BLOCKED_PLACEHOLDER).
        * 3 rows with placeholder suffix ``10.1016/example`` etc. (BLOCKED_PLACEHOLDER).
        * 2 rows with placeholder source ``Smirnov2014`` (BLOCKED_PLACEHOLDER_SOURCE).
    Total: 156 + 4 + 3 + 2 + 3 + 2 = 170.
    """
    seed = [
        "10.1016/j.jnucmat.2020.152307",
        "10.1016/j.jnucmat.2019.07.004",
        "10.1016/j.jnucmat.2019.03.017",
        "10.1016/j.jnucmat.2018.11.008",
        "10.1016/j.jnucmat.2018.04.019",
        "10.1016/j.jnucmat.2017.12.007",
        "10.1016/j.jnucmat.2017.04.003",
        "10.1016/j.jnucmat.2016.11.001",
        "10.1016/j.jnucmat.2016.02.021",
        "10.1016/j.jnucmat.2015.11.012",
        "10.1016/j.nucengdes.2020.110790",
        "10.1016/j.nucengdes.2019.05.014",
        "10.1016/j.nucengdes.2018.10.015",
        "10.1016/j.nucengdes.2017.11.008",
        "10.1016/j.nucengdes.2016.01.023",
        "10.1016/j.nucengdes.2015.12.003",
    ]

    rows: list[StagingRow] = []
    for i in range(170):
        rid = uuid.UUID(int=i + 1)
        if i < 156:
            rows.append(
                StagingRow(
                    id=rid,
                    source=f"Owen2023-{i}",
                    source_doi=seed[i % len(seed)],
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
                    source_doi=seed[i % len(seed)],
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Prescreen — must deterministically block every known fake pattern
# ---------------------------------------------------------------------------


class TestPrescreenBlocksKnownFakes:
    """Every BLOCKED_* verdict fires on its known-bad pattern."""

    @pytest.mark.parametrize(
        "source, source_doi",
        [
            ("Smirnov2014", "10.1016/j.jnucmat.2020.152307"),
            ("MP-DFT", "10.1016/j.jnucmat.2019.07.004"),
            ("Finkelstein2001", "10.1016/j.jnucmat.2018.11.008"),
            ("MP-Experimental", None),
            ("DEMO", None),
        ],
    )
    def test_placeholder_source_blocks_even_when_doi_looks_valid(
        self, source: str, source_doi: str | None
    ) -> None:
        result = prescreen_doi(source=source, source_doi=source_doi)
        assert result.verdict == PrescreenVerdict.BLOCKED_PLACEHOLDER_SOURCE
        assert source in result.reason

    def test_placeholder_source_tokens_are_complete(self) -> None:
        # Guards the placeholder list against accidental edits — every
        # token referenced by gap_fill_service.py L1 cache stub MUST
        # be in the blocked set.
        assert {"Smirnov2014", "MP-DFT", "Finkelstein2001"} <= PLACEHOLDER_SOURCE_TOKENS

    @pytest.mark.parametrize(
        "source_doi",
        [
            None,
            "",
            "   ",
            "None",
            " null ",
        ],
    )
    def test_null_or_empty_doi_blocks(self, source_doi: str | None) -> None:
        result = prescreen_doi(source="Owen2023", source_doi=source_doi)
        assert result.verdict == PrescreenVerdict.BLOCKED_NULL

    @pytest.mark.parametrize(
        "source_doi",
        [
            "not-a-doi",
            "11.1016/j.jnucmat.2020.152307",   # registrant code too short
            "10.abc/j.jnucmat.2020.152307",    # non-numeric registrant
            "10.1016",                          # missing slash+suffix
            "10.1016/ has space",
        ],
    )
    def test_format_broken_doi_blocks(self, source_doi: str) -> None:
        result = prescreen_doi(source="Owen2023", source_doi=source_doi)
        assert result.verdict == PrescreenVerdict.BLOCKED_FORMAT

    @pytest.mark.parametrize(
        "source_doi",
        [
            "10.0000/anything",
            "10.1234/anything",
            "10.5555/anything",
        ],
    )
    def test_placeholder_registrant_blocks(self, source_doi: str) -> None:
        result = prescreen_doi(source="Owen2023", source_doi=source_doi)
        assert result.verdict == PrescreenVerdict.BLOCKED_PLACEHOLDER

    @pytest.mark.parametrize(
        "source_doi",
        [
            "10.1016/placeholder",
            "10.1016/Placeholder",
            "10.1016/PLACEHOLDER",
            "10.1016/fake",
            "10.1016/example",
            "10.1016/demo",
            "10.1016/test",
            "10.1016/sample",
            "10.1016/n/a",
            "10.1016/tbd",
            "10.1016/todo",
            "10.1016/xxx",
        ],
    )
    def test_placeholder_suffix_blocks(self, source_doi: str) -> None:
        result = prescreen_doi(source="Owen2023", source_doi=source_doi)
        assert result.verdict == PrescreenVerdict.BLOCKED_PLACEHOLDER

    def test_source_token_check_runs_before_null_check(self) -> None:
        # Smirnov2014 + null DOI → BLOCKED_PLACEHOLDER_SOURCE, not BLOCKED_NULL.
        # This guarantees the manifest reason codes let an operator triage
        # the historical contamination vector first.
        result = prescreen_doi(source="Smirnov2014", source_doi=None)
        assert result.verdict == PrescreenVerdict.BLOCKED_PLACEHOLDER_SOURCE

    def test_doi_regex_matches_runtime_validation(self) -> None:
        # The gate regex MUST match the runtime regex from doi_fetcher.py
        # exactly, otherwise a DOI admitted by the gate would still fail
        # extraction downstream.
        runtime = re.compile(r"^10\.\d{4,9}/[^\s]+$")
        assert DOI_REGEX.pattern == runtime.pattern


class TestPrescreenAcceptsRealDOIs:
    """Every curated real DOI from seed_dois.json must PASS."""

    def test_all_curated_seed_dois_pass(self, curated_real_dois: list[str]) -> None:
        assert len(curated_real_dois) >= 50, (
            "expected the curated seed to cover >=50 DOIs; the test "
            "fixture's whole point is to prove the gate doesn't reject "
            "real nuclear-materials DOIs"
        )
        for doi in curated_real_dois:
            result = prescreen_doi(source="Owen2023", source_doi=doi)
            assert result.verdict == PrescreenVerdict.PASS, (
                f"real DOI {doi!r} was rejected: {result.reason}"
            )


# ---------------------------------------------------------------------------
# Sample selection — deterministic, well-sized, idempotent
# ---------------------------------------------------------------------------


class TestSampleSelection:
    def test_empty_rows_returns_empty_set(self) -> None:
        assert select_sample([], sample_rate=0.30, seed=42) == set()

    def test_sample_size_is_ceil_of_cohort_times_rate(self) -> None:
        rows = [StagingRow(id=uuid.UUID(int=i + 1), source="S", source_doi="10.1016/x") for i in range(170)]
        ids = select_sample(rows, sample_rate=0.30, seed=20260830)
        # ceil(170 * 0.30) == 51
        assert len(ids) == 51

    def test_sample_is_reproducible_with_same_seed(self) -> None:
        rows = [StagingRow(id=uuid.UUID(int=i + 1), source="S", source_doi="10.1016/x") for i in range(170)]
        a = select_sample(rows, sample_rate=0.30, seed=20260830)
        b = select_sample(rows, sample_rate=0.30, seed=20260830)
        assert a == b

    def test_different_seeds_can_yield_different_samples(self) -> None:
        rows = [StagingRow(id=uuid.UUID(int=i + 1), source="S", source_doi="10.1016/x") for i in range(170)]
        a = select_sample(rows, sample_rate=0.30, seed=1)
        b = select_sample(rows, sample_rate=0.30, seed=2)
        assert a != b

    def test_full_sample_when_rate_is_one(self) -> None:
        rows = [StagingRow(id=uuid.UUID(int=i + 1), source="S", source_doi="10.1016/x") for i in range(10)]
        ids = select_sample(rows, sample_rate=1.0, seed=42)
        assert len(ids) == 10

    def test_invalid_rate_raises(self) -> None:
        rows = [StagingRow(id=uuid.UUID(int=1), source="S", source_doi="10.1016/x")]
        with pytest.raises(ValueError):
            select_sample(rows, sample_rate=0.0)
        with pytest.raises(ValueError):
            select_sample(rows, sample_rate=1.5)

    def test_sample_uses_only_rows_in_input(self) -> None:
        rows = [StagingRow(id=uuid.UUID(int=i + 1), source="S", source_doi="10.1016/x") for i in range(170)]
        ids = select_sample(rows, sample_rate=0.30, seed=20260830)
        # Every selected id must be one of the row ids we passed in.
        valid = {r.id for r in rows}
        assert ids <= valid


# ---------------------------------------------------------------------------
# Cross-validation — both backends must agree to VALIDATE
# ---------------------------------------------------------------------------


class TestCrossValidate:
    def test_both_backends_hit_and_agree(self) -> None:
        meta = DOIMetadata(found=True, title="X", first_author="Y", year=2020)
        a = InMemoryBackend("crossref", {"10.1016/x": meta})
        b = InMemoryBackend("openalex", {"10.1016/x": meta})
        result = cross_validate("10.1016/x", a, b)
        assert result.verdict == ValidationVerdict.VALIDATED

    def test_neither_backend_hits(self) -> None:
        a = InMemoryBackend("crossref")
        b = InMemoryBackend("openalex")
        result = cross_validate("10.1016/x", a, b)
        assert result.verdict == ValidationVerdict.VALIDATED_FAIL

    def test_only_one_backend_hits_is_partial(self) -> None:
        meta = DOIMetadata(found=True, title="X", first_author="Y", year=2020)
        a = InMemoryBackend("crossref", {"10.1016/x": meta})
        b = InMemoryBackend("openalex")
        result = cross_validate("10.1016/x", a, b)
        assert result.verdict == ValidationVerdict.VALIDATED_PARTIAL

    def test_metadata_disagreement_is_fail(self) -> None:
        a = InMemoryBackend(
            "crossref",
            {"10.1016/x": DOIMetadata(found=True, title="X", first_author="Y", year=2020)},
        )
        b = InMemoryBackend(
            "openalex",
            {"10.1016/x": DOIMetadata(found=True, title="Z", first_author="Y", year=2020)},
        )
        result = cross_validate("10.1016/x", a, b)
        assert result.verdict == ValidationVerdict.VALIDATED_FAIL
        assert result.title_match is False

    def test_first_author_disagreement_is_fail(self) -> None:
        a = InMemoryBackend(
            "crossref",
            {"10.1016/x": DOIMetadata(found=True, title="X", first_author="Y", year=2020)},
        )
        b = InMemoryBackend(
            "openalex",
            {"10.1016/x": DOIMetadata(found=True, title="X", first_author="Z", year=2020)},
        )
        result = cross_validate("10.1016/x", a, b)
        assert result.verdict == ValidationVerdict.VALIDATED_FAIL
        assert result.first_author_match is False

    def test_year_disagreement_is_fail(self) -> None:
        a = InMemoryBackend(
            "crossref",
            {"10.1016/x": DOIMetadata(found=True, title="X", first_author="Y", year=2020)},
        )
        b = InMemoryBackend(
            "openalex",
            {"10.1016/x": DOIMetadata(found=True, title="X", first_author="Y", year=2021)},
        )
        result = cross_validate("10.1016/x", a, b)
        assert result.verdict == ValidationVerdict.VALIDATED_FAIL
        assert result.year_match is False

    def test_in_memory_backend_register_method(self) -> None:
        # register() is the public mutation entry-point used by the CLI
        # to bulk-load a Crossref/OpenAlex response map before the gate
        # run. Locks the API surface so refactors don't silently break it.
        backend = InMemoryBackend("crossref")
        backend.register("10.1016/x", DOIMetadata(found=True, title="T"))
        assert backend.lookup("10.1016/x").found is True
        assert backend.lookup("10.1016/missing").found is False

    def test_metadata_comparison_is_whitespace_and_case_tolerant(self) -> None:
        a = InMemoryBackend(
            "crossref",
            {"10.1016/x": DOIMetadata(found=True, title="  X  ", first_author="Y", year=2020)},
        )
        b = InMemoryBackend(
            "openalex",
            {"10.1016/x": DOIMetadata(found=True, title="x", first_author="Y", year=2020)},
        )
        result = cross_validate("10.1016/x", a, b)
        assert result.verdict == ValidationVerdict.VALIDATED


# ---------------------------------------------------------------------------
# Manifest — synthetic 170-row cohort end-to-end
# ---------------------------------------------------------------------------


class TestManifestOnSynthetic170RowCohort:
    def test_summary_counts_match_cohort_composition(
        self, synthetic_170_cohort: list[StagingRow]
    ) -> None:
        backend_a = InMemoryBackend(
            "crossref",
            {row.source_doi: DOIMetadata(found=True, title="X", first_author="Y", year=2020)
             for row in synthetic_170_cohort if row.source_doi},
        )
        backend_b = InMemoryBackend(
            "openalex",
            {row.source_doi: DOIMetadata(found=True, title="X", first_author="Y", year=2020)
             for row in synthetic_170_cohort if row.source_doi},
        )
        decisions, summary = build_admission_manifest(
            synthetic_170_cohort,
            backend_a=backend_a,
            backend_b=backend_b,
            sample_rate=0.30,
            seed=20260830,
        )

        assert summary.total_rows == 170
        # 4 null + 3 format + 2 placeholder-reg + 3 placeholder-suf + 2 placeholder-source
        assert summary.prescreen_blocked == 14
        assert summary.prescreen_pass == 156
        # ceil(156 * 0.30) == 47
        assert summary.sample_size == 47
        # The 2 placeholder-source rows have source=Smirnov2014 — already
        # blocked by prescreen so they don't enter the sample cohort.
        # Every sampled row's DOI is in the backend map → all VALIDATED.
        assert summary.validated == summary.sample_size
        assert summary.validated_partial == 0
        assert summary.validated_fail == 0
        # etl_ok = prescreen_pass + (no partial/fail contributions from sample)
        # = 156 (sampled rows are admitted because they validated)
        assert summary.etl_ok == 156
        assert summary.etl_blocked == 14

        # Every blocking reason code we expect shows up in the histogram.
        assert summary.blocked_by_reason["BLOCKED_NULL"] == 4
        assert summary.blocked_by_reason["BLOCKED_FORMAT"] == 3
        assert summary.blocked_by_reason["BLOCKED_PLACEHOLDER"] == 5
        assert summary.blocked_by_reason["BLOCKED_PLACEHOLDER_SOURCE"] == 2

        # Decision list length matches cohort size; every row has an entry.
        assert len(decisions) == 170
        blocked_count = sum(1 for d in decisions if not d.etl_ok)
        assert blocked_count == 14

    def test_dry_run_without_backends_admits_all_prescreen_passes(
        self, synthetic_170_cohort: list[StagingRow]
    ) -> None:
        decisions, summary = build_admission_manifest(
            synthetic_170_cohort,
            backend_a=None,
            backend_b=None,
            sample_rate=0.30,
            seed=20260830,
        )
        # Prescreen-only — every passing row is admitted on the prescreen.
        assert summary.etl_ok == 156
        assert summary.etl_blocked == 14
        # Validation field is None for every decision (no backends wired).
        assert all(d.validation is None for d in decisions)
        # Sampling still happens so the manifest's ``sampled`` flag is
        # populated consistently for the C-S1 handoff.
        assert summary.sample_size == 47
        sampled = sum(1 for d in decisions if d.sampled)
        assert sampled == 47

    def test_manifest_is_jsonable_and_round_trips(
        self, synthetic_170_cohort: list[StagingRow]
    ) -> None:
        backend_a = InMemoryBackend(
            "crossref",
            {row.source_doi: DOIMetadata(found=True, title="X", first_author="Y", year=2020)
             for row in synthetic_170_cohort if row.source_doi},
        )
        backend_b = InMemoryBackend(
            "openalex",
            {row.source_doi: DOIMetadata(found=True, title="X", first_author="Y", year=2020)
             for row in synthetic_170_cohort if row.source_doi},
        )
        decisions, summary = build_admission_manifest(
            synthetic_170_cohort,
            backend_a=backend_a,
            backend_b=backend_b,
        )
        payload = manifest_to_jsonable(
            decisions,
            summary,
            issue="NFM-3871",
            generated_at="2026-08-31T00:00:00Z",
        )
        assert payload["schema_version"] == "1.0"
        assert payload["issue"] == "NFM-3871"
        assert payload["summary"]["total_rows"] == 170
        # Round-trip — serialise then parse; the row list must still match.
        blob = json.dumps(payload)
        parsed = json.loads(blob)
        assert len(parsed["rows"]) == 170
        # Spot-check: every prescreen=BLOCKED row carries a blocking_reason.
        blocked = [r for r in parsed["rows"] if not r["etl_ok"]]
        assert blocked, "synthetic cohort must have at least one blocked row"
        for row in blocked:
            assert row["blocking_reason"]
            assert row["prescreen"]["verdict"] != "PASS"

    def test_sampled_rows_with_partial_validation_are_blocked(
        self, synthetic_170_cohort: list[StagingRow]
    ) -> None:
        # Crossref returns all the DOIs, OpenAlex returns nothing →
        # every sampled row is VALIDATED_PARTIAL and etl_ok=False.
        backend_a = InMemoryBackend(
            "crossref",
            {row.source_doi: DOIMetadata(found=True, title="X", first_author="Y", year=2020)
             for row in synthetic_170_cohort if row.source_doi},
        )
        backend_b = InMemoryBackend("openalex")
        decisions, summary = build_admission_manifest(
            synthetic_170_cohort,
            backend_a=backend_a,
            backend_b=backend_b,
        )
        # All prescreen-passes that happened to be sampled are blocked.
        assert summary.validated == 0
        assert summary.validated_partial == summary.sample_size
        assert summary.validated_fail == 0
        # etl_ok is only the unsampled prescreen-passing rows.
        assert summary.etl_ok == summary.prescreen_pass - summary.sample_size
        assert summary.etl_blocked == (
            summary.prescreen_blocked + summary.sample_size
        )
        # Each sampled blocked row carries a non-null blocking_reason
        # referencing the partial-validation detail.
        sampled_blocked = [
            d for d in decisions if d.sampled and not d.etl_ok
        ]
        assert len(sampled_blocked) == summary.sample_size
        for d in sampled_blocked:
            assert d.blocking_reason is not None
            assert "missed" in d.blocking_reason

    def test_sampled_rows_with_metadata_disagreement_are_blocked(
        self, synthetic_170_cohort: list[StagingRow]
    ) -> None:
        # Both backends hit, but title disagrees → every sampled row
        # is VALIDATED_FAIL.
        backend_a = InMemoryBackend(
            "crossref",
            {row.source_doi: DOIMetadata(found=True, title="X", first_author="Y", year=2020)
             for row in synthetic_170_cohort if row.source_doi},
        )
        backend_b = InMemoryBackend(
            "openalex",
            {row.source_doi: DOIMetadata(found=True, title="WRONG", first_author="Y", year=2020)
             for row in synthetic_170_cohort if row.source_doi},
        )
        _, summary = build_admission_manifest(
            synthetic_170_cohort,
            backend_a=backend_a,
            backend_b=backend_b,
        )
        assert summary.validated == 0
        assert summary.validated_partial == 0
        assert summary.validated_fail == summary.sample_size
        assert summary.blocked_by_reason["VALIDATED_FAIL"] == summary.sample_size


# ---------------------------------------------------------------------------
# End-to-end stats sanity — gate is strict enough to catch the 1.2 % mix-in
# ---------------------------------------------------------------------------


class TestStatsSanity:
    """The C-D7 amendment was driven by the 1.2 % historical contamination
    rate; verify the gate's deterministic pre-screen catches at minimum the
    known placeholder patterns, regardless of the 30 % sample."""

    def test_placeholder_dois_are_blocked_before_any_sample(
        self, synthetic_170_cohort: list[StagingRow]
    ) -> None:
        # With NO backends wired, prescreen alone must still block every
        # known-bad row — this is the A+修正案's whole point.
        _, summary = build_admission_manifest(
            synthetic_170_cohort,
            backend_a=None,
            backend_b=None,
        )
        # 4 null + 3 format + 2 placeholder-reg + 3 placeholder-suf + 2
        # placeholder-source = 14 blocked before any sampling happens.
        assert summary.prescreen_blocked == 14
        assert summary.prescreen_pass == 156
        # etl_ok in dry-run = prescreen_pass (every passing row admitted on
        # prescreen alone when no backends are wired).
        assert summary.etl_ok == 156
