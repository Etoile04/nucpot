"""DOI pre-screen + statistical validation ETL admission gate (NFM-3871 / C-I1).

Implements the A+修正案 amendment to C-D7 (NFM-3838): for every row in
``_ref_gap_fill_staging`` we run a deterministic pre-screen that blocks
known fake/placeholder DOI failure modes, then sample 30 % of the
passing rows for a secondary-source Crossref/OpenAlex cross-check.
Only rows that pass both gates are admitted to the ETL pipeline that
C-S1 will execute.

Design notes
------------

The script-side pre-screen is intentionally rule-based (regex + set
membership) rather than network-dependent: a placeholder DOI like
``10.0000/placeholder`` must be blocked before any HTTP call so a
network outage cannot accidentally admit a known-bad value. The
secondary check then catches the cases the rules miss (1.2 % historical
mix-in rate per C-D7).

Pre-screen blocked reasons (each row gets exactly one):

* ``BLOCKED_NULL`` — ``source_doi`` is NULL/empty/``"None"`` literal.
* ``BLOCKED_FORMAT`` — value does not match ``^10\\.\\d{4,9}/[^\\s]+$``.
* ``BLOCKED_PLACEHOLDER`` — value matches a known placeholder pattern
  (10.0000/…, 10.1234/…, 10.5555/…, suffix ``placeholder``/``fake``/
  ``example``/``demo``/``test``/``n/a``/``sample``).
* ``BLOCKED_PLACEHOLDER_SOURCE`` — ``source`` column is one of the
  gap_fill_service.py L1 placeholder author-year strings
  (``Smirnov2014``, ``MP-DFT``, ``Finkelstein2001``) even when the DOI
  column happens to be populated; the row is still rejected because
  the source attribution is the historical contamination vector.

For passing rows we deterministically sample 30 % with a
``random.Random(seed)`` so two runs over the same data produce
identical manifests (required for the ETL gate to be reproducible
during incident review).

Each sampled row is then cross-validated by two backends (Crossref and
OpenAlex). A row is ``VALIDATED`` only when both backends agree on
title/first-author/year; ``VALIDATED_PARTIAL`` when only one backend
returned a hit; ``VALIDATED_FAIL`` when neither backend returned a hit
or the two backends disagree on a metadata field.

Final ETL admission (column ``etl_ok``) requires both prescreen=``PASS``
and (sampled) validation=``VALIDATED``. Unsampled rows that pass the
prescreen are admitted on the prescreen alone (the A+修正案 explicitly
chose 100 % pre-screen over 100 % validation to keep the work bounded
— see C-D7 amendment rationale).

Manifest shape (JSON, written by ``doi_etl_admit.py``)::

    {
      "schema_version": "1.0",
      "issue": "NFM-3871",
      "generated_at": "2026-08-31T...Z",
      "summary": {
        "total_rows": 170,
        "prescreen_pass": 154,
        "prescreen_blocked": 16,
        "sample_size": 46,
        "validated": 41,
        "validated_partial": 3,
        "validated_fail": 2,
        "etl_ok": 149,
        "etl_blocked": 21
      },
      "rows": [
        {
          "row_id": "uuid",
          "source": "Smirnov2014",
          "source_doi": null,
          "prescreen": {"verdict": "BLOCKED_PLACEHOLDER_SOURCE", "reason": "..."},
          "sampled": false,
          "validation": null,
          "etl_ok": false
        },
        ...
      ]
    }
"""

from __future__ import annotations

import enum
import random
import re
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# DOI format + known-bad patterns
# ---------------------------------------------------------------------------

#: Standard DOI format. Mirrors ``doi_fetcher.DOI_REGEX`` so the gate
#: matches the runtime validation regex 1:1.
DOI_REGEX: re.Pattern[str] = re.compile(r"^10\.\d{4,9}/[^\s]+$")

#: Registrant prefixes that are reserved as test/example ranges per
#: the Crossref / DataCite guidance. They never point to a real paper.
PLACEHOLDER_REGISTRANTS: frozenset[str] = frozenset(
    {
        "10.0000",
        "10.00000",
        "10.1234",
        "10.12345",
        "10.5555",
        "10.9999",
    }
)

#: Suffix tokens that, regardless of registrant, identify a placeholder
#: paper. Case-insensitive match against the part after ``10.NNNN/``.
PLACEHOLDER_SUFFIX_TOKENS: tuple[str, ...] = (
    "placeholder",
    "fake",
    "example",
    "demo",
    "test",
    "sample",
    "n/a",
    "na",
    "tbd",
    "todo",
    "xxx",
)

#: Combined regex: registrant in PLACEHOLDER_REGISTRANTS OR suffix in
#: PLACEHOLDER_SUFFIX_TOKENS (anchored after the slash, case-insensitive).
PLACEHOLDER_SUFFIX_RE: re.Pattern[str] = re.compile(
    r"^(?:" + "|".join(re.escape(tok) for tok in PLACEHOLDER_SUFFIX_TOKENS) + r")$",
    re.IGNORECASE,
)

#: ``source``-column placeholder strings emitted by ``gap_fill_service.py``
#: L1 cache stub. These mark rows as historical demo data even when the
#: ``source_doi`` column happens to be populated (which is itself an
#: upstream inconsistency).
PLACEHOLDER_SOURCE_TOKENS: frozenset[str] = frozenset(
    {
        "Smirnov2014",
        "MP-DFT",
        "Finkelstein2001",
        "MP-Experimental",
        "DEMO",
    }
)


# ---------------------------------------------------------------------------
# Verdict enums
# ---------------------------------------------------------------------------


class PrescreenVerdict(str, enum.Enum):
    """Outcome of the deterministic DOI pre-screen."""

    PASS = "PASS"
    BLOCKED_NULL = "BLOCKED_NULL"
    BLOCKED_FORMAT = "BLOCKED_FORMAT"
    BLOCKED_PLACEHOLDER = "BLOCKED_PLACEHOLDER"
    BLOCKED_PLACEHOLDER_SOURCE = "BLOCKED_PLACEHOLDER_SOURCE"


class ValidationVerdict(str, enum.Enum):
    """Outcome of the secondary-source Crossref/OpenAlex cross-check."""

    VALIDATED = "VALIDATED"
    VALIDATED_PARTIAL = "VALIDATED_PARTIAL"
    VALIDATED_FAIL = "VALIDATED_FAIL"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrescreenResult:
    """Pre-screen decision for one row."""

    verdict: PrescreenVerdict
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    """Secondary-source cross-check outcome for one row."""

    verdict: ValidationVerdict
    backend_a_hit: bool
    backend_b_hit: bool
    title_match: bool | None
    first_author_match: bool | None
    year_match: bool | None
    detail: str


@dataclass(frozen=True)
class AdmissionDecision:
    """Row-level ETL admission decision."""

    row_id: str
    source: str
    source_doi: str | None
    prescreen: PrescreenResult
    sampled: bool
    validation: ValidationResult | None
    etl_ok: bool
    blocking_reason: str | None = None


@dataclass(frozen=True)
class AdmissionSummary:
    """Aggregate counts for the manifest header."""

    total_rows: int
    prescreen_pass: int
    prescreen_blocked: int
    sample_size: int
    validated: int
    validated_partial: int
    validated_fail: int
    etl_ok: int
    etl_blocked: int
    blocked_by_reason: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class StagingRow:
    """Minimal projection of a ``_ref_gap_fill_staging`` row."""

    id: uuid.UUID
    source: str
    source_doi: str | None


# ---------------------------------------------------------------------------
# Pre-screen
# ---------------------------------------------------------------------------


def _strip_doi(value: str | None) -> str | None:
    """Normalise a DOI value: strip whitespace, treat nullish sentinels as absent.

    The case-insensitive match covers the two JSON encodings a downstream
    caller can produce — Python's ``str(None)`` is ``"None"``, while a JSON
    deserialiser that lost its null sentinel emits ``"null"``. Both must
    route to BLOCKED_NULL so a regression in ETL null-handling (NFM-3518
    class) cannot accidentally slip a sentinel through to BLOCKED_FORMAT.
    """
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v.lower() in {"none", "null"}:
        return None
    return v


def prescreen_doi(
    *,
    source: str | None,
    source_doi: str | None,
) -> PrescreenResult:
    """Run the deterministic pre-screen on one row.

    Order matters and is documented here because the manifest reason
    codes are how an operator triages blocked rows:

    1. Source-token check first — these are the historical contamination
       vector (Smirnov2014 etc.) and trump DOI-level checks because
       the source attribution itself is unreliable even if the DOI
       column happens to look well-formed.
    2. Null/empty DOI.
    3. DOI format check.
    4. Placeholder pattern check (registrant + suffix).
    """
    src = (source or "").strip()
    if src in PLACEHOLDER_SOURCE_TOKENS:
        return PrescreenResult(
            verdict=PrescreenVerdict.BLOCKED_PLACEHOLDER_SOURCE,
            reason=(
                f"source column matches gap_fill L1 placeholder token {src!r}; "
                "historical demo data, no real paper attribution"
            ),
        )

    doi = _strip_doi(source_doi)
    if doi is None:
        return PrescreenResult(
            verdict=PrescreenVerdict.BLOCKED_NULL,
            reason="source_doi is null or empty",
        )

    if not DOI_REGEX.match(doi):
        return PrescreenResult(
            verdict=PrescreenVerdict.BLOCKED_FORMAT,
            reason=f"source_doi {doi!r} does not match DOI_REGEX",
        )

    registrant, _, suffix = doi.partition("/")
    if registrant in PLACEHOLDER_REGISTRANTS:
        return PrescreenResult(
            verdict=PrescreenVerdict.BLOCKED_PLACEHOLDER,
            reason=(
                f"source_doi {doi!r} uses reserved placeholder registrant "
                f"{registrant!r}"
            ),
        )

    if PLACEHOLDER_SUFFIX_RE.match(suffix):
        return PrescreenResult(
            verdict=PrescreenVerdict.BLOCKED_PLACEHOLDER,
            reason=(
                f"source_doi {doi!r} suffix {suffix!r} is a known placeholder "
                "token"
            ),
        )

    return PrescreenResult(verdict=PrescreenVerdict.PASS, reason="ok")


# ---------------------------------------------------------------------------
# Secondary-source backends
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DOIMetadata:
    """Metadata returned by a DOI secondary-source backend."""

    found: bool
    title: str | None = None
    first_author: str | None = None
    year: int | None = None


@runtime_checkable
class DOISecondarySourceBackend(Protocol):
    """Protocol for a DOI secondary-source backend (Crossref, OpenAlex, ...)."""

    name: str

    def lookup(self, doi: str) -> DOIMetadata:
        """Resolve *doi* and return metadata.

        Returns ``DOIMetadata(found=False)`` on any failure (network,
        404, 5xx, parse error). Callers must treat ``found=False`` as
        "this backend has no opinion", not as a negative signal.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory backend (tests + dry-run)
# ---------------------------------------------------------------------------


class InMemoryBackend:
    """In-memory DOI backend backed by a static mapping.

    Used by tests and the ``--dry-run`` CLI mode. The mapping is
    ``{doi: DOIMetadata}``; any DOI not in the map returns
    ``DOIMetadata(found=False)``.
    """

    def __init__(
        self,
        name: str,
        mapping: Mapping[str, DOIMetadata] | None = None,
    ) -> None:
        self.name = name
        self._mapping: dict[str, DOIMetadata] = dict(mapping or {})

    def register(self, doi: str, meta: DOIMetadata) -> None:
        self._mapping[doi] = meta

    def lookup(self, doi: str) -> DOIMetadata:
        return self._mapping.get(doi, DOIMetadata(found=False))


# ---------------------------------------------------------------------------
# Secondary-source cross-check
# ---------------------------------------------------------------------------


def _safe_meta(meta: DOIMetadata | None) -> DOIMetadata:
    return meta if meta is not None else DOIMetadata(found=False)


def cross_validate(
    doi: str,
    backend_a: DOISecondarySourceBackend,
    backend_b: DOISecondarySourceBackend,
) -> ValidationResult:
    """Cross-validate *doi* against two independent secondary-source backends.

    Both backends MUST return ``found=True`` AND agree on title, first
    author, and year for the verdict to be ``VALIDATED``. A partial hit
    (``VALIDATED_PARTIAL``) is logged but does NOT contribute to
    ``etl_ok``; this matches the A+修正案 amendment which required the
    30 % sample to be cross-validated, not single-sourced.
    """
    a = _safe_meta(backend_a.lookup(doi))
    b = _safe_meta(backend_b.lookup(doi))

    if not a.found and not b.found:
        return ValidationResult(
            verdict=ValidationVerdict.VALIDATED_FAIL,
            backend_a_hit=False,
            backend_b_hit=False,
            title_match=None,
            first_author_match=None,
            year_match=None,
            detail=f"neither {backend_a.name} nor {backend_b.name} returned a hit",
        )

    if a.found != b.found:
        loser = backend_b.name if a.found else backend_a.name
        return ValidationResult(
            verdict=ValidationVerdict.VALIDATED_PARTIAL,
            backend_a_hit=a.found,
            backend_b_hit=b.found,
            title_match=None,
            first_author_match=None,
            year_match=None,
            detail=f"only one backend hit ({loser} missed)",
        )

    # Both backends returned a hit — compare metadata.
    title_match = _norm(a.title) == _norm(b.title)
    first_author_match = _norm(a.first_author) == _norm(b.first_author)
    year_match = a.year == b.year

    if title_match and first_author_match and year_match:
        return ValidationResult(
            verdict=ValidationVerdict.VALIDATED,
            backend_a_hit=True,
            backend_b_hit=True,
            title_match=True,
            first_author_match=True,
            year_match=True,
            detail=(
                f"{backend_a.name} and {backend_b.name} agree on "
                f"title/first_author/year"
            ),
        )

    disagreements = []
    if not title_match:
        disagreements.append("title")
    if not first_author_match:
        disagreements.append("first_author")
    if not year_match:
        disagreements.append("year")
    return ValidationResult(
        verdict=ValidationVerdict.VALIDATED_FAIL,
        backend_a_hit=True,
        backend_b_hit=True,
        title_match=title_match,
        first_author_match=first_author_match,
        year_match=year_match,
        detail=(
            f"{backend_a.name} and {backend_b.name} disagree on "
            + ", ".join(disagreements)
        ),
    )


def _norm(s: str | None) -> str:
    """Lowercase + collapse whitespace for tolerant metadata comparison."""
    if s is None:
        return ""
    return " ".join(s.lower().split())


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def select_sample(
    rows: list[StagingRow],
    *,
    sample_rate: float = 0.30,
    seed: int = 20260830,
) -> set[uuid.UUID]:
    """Deterministically sample *sample_rate* of *rows*.

    Returns the set of row IDs selected. Uses ``random.Random(seed)``
    so the manifest is reproducible across runs — required so the C-S1
    ETL pass consumes the same row set the C-I1 admission manifest
    declared.

    Sampling is over the rows that PASS the pre-screen only (the
    caller passes already-filtered rows). For *n* passing rows we
    select ``ceil(n * sample_rate)`` rows; the minimum sample size of
    50 from C-D7 is enforced when the cohort is large enough — for
    the current 170-row cohort that is moot (≥ 50 always), but we
    encode the rule here so future cohorts behave identically.
    """
    if not 0.0 < sample_rate <= 1.0:
        raise ValueError(f"sample_rate must be in (0, 1], got {sample_rate!r}")
    if not rows:
        return set()

    n = len(rows)
    k = max(1, (n * sample_rate).__ceil__()) if sample_rate < 1.0 else n

    rng = random.Random(seed)
    # Sort by id for stability before sampling so the random.choice()
    # sequence is fully determined by the seed.
    ordered = sorted(rows, key=lambda r: r.id)
    indices = rng.sample(range(n), k)
    return {ordered[i].id for i in indices}


def build_admission_manifest(
    rows: list[StagingRow],
    *,
    backend_a: DOISecondarySourceBackend | None = None,
    backend_b: DOISecondarySourceBackend | None = None,
    sample_rate: float = 0.30,
    seed: int = 20260830,
) -> tuple[list[AdmissionDecision], AdmissionSummary]:
    """Run the full gate over *rows* and return per-row decisions + summary.

    Parameters
    ----------
    rows:
        All staging rows to evaluate (no pre-filtering by the caller).
    backend_a, backend_b:
        Secondary-source backends used to cross-validate the sampled
        rows. Either may be ``None`` for dry-run / CI smoke tests, in
        which case sampled rows that pass prescreen are admitted
        without a secondary check (``etl_ok=True``) and unsampled
        prescreen-passes are also admitted. The ``ValidationResult``
        field is left ``None`` to signal the gate was skipped.
    sample_rate:
        Fraction of prescreen-passing rows to sample (default 0.30 per
        C-D7 amendment).
    seed:
        RNG seed for the sampler (default 20260830, the day C-D7
        amendment was decided).
    """
    # Phase 1: pre-screen all rows.
    prescreen_results: dict[uuid.UUID, PrescreenResult] = {}
    for row in rows:
        prescreen_results[row.id] = prescreen_doi(
            source=row.source,
            source_doi=row.source_doi,
        )

    passing_rows = [r for r in rows if prescreen_results[r.id].verdict == PrescreenVerdict.PASS]

    # Phase 2: sample (only meaningful if we have backends to validate
    # against; in dry-run we still sample so the manifest's
    # ``sampled`` field is populated consistently).
    sampled_ids = select_sample(passing_rows, sample_rate=sample_rate, seed=seed)

    decisions: list[AdmissionDecision] = []
    validated = validated_partial = validated_fail = 0
    etl_ok = etl_blocked = 0
    blocked_by_reason: dict[str, int] = {}

    for row in rows:
        ps = prescreen_results[row.id]
        is_sampled = row.id in sampled_ids
        validation: ValidationResult | None = None

        if ps.verdict != PrescreenVerdict.PASS:
            etl_blocked += 1
            blocked_by_reason[ps.verdict.value] = (
                blocked_by_reason.get(ps.verdict.value, 0) + 1
            )
            decisions.append(
                AdmissionDecision(
                    row_id=str(row.id),
                    source=row.source,
                    source_doi=row.source_doi,
                    prescreen=ps,
                    sampled=is_sampled,
                    validation=None,
                    etl_ok=False,
                    blocking_reason=ps.reason,
                )
            )
            continue

        if is_sampled and backend_a is not None and backend_b is not None:
            doi = (row.source_doi or "").strip()
            validation = cross_validate(doi, backend_a, backend_b)
            if validation.verdict == ValidationVerdict.VALIDATED:
                validated += 1
                row_etl_ok = True
            elif validation.verdict == ValidationVerdict.VALIDATED_PARTIAL:
                validated_partial += 1
                row_etl_ok = False
                blocked_by_reason["VALIDATED_PARTIAL"] = (
                    blocked_by_reason.get("VALIDATED_PARTIAL", 0) + 1
                )
            else:
                validated_fail += 1
                row_etl_ok = False
                blocked_by_reason["VALIDATED_FAIL"] = (
                    blocked_by_reason.get("VALIDATED_FAIL", 0) + 1
                )
        else:
            # No backends wired (dry-run) or row not sampled — admit on
            # prescreen alone.
            row_etl_ok = True

        if row_etl_ok:
            etl_ok += 1
        else:
            etl_blocked += 1

        decisions.append(
            AdmissionDecision(
                row_id=str(row.id),
                source=row.source,
                source_doi=row.source_doi,
                prescreen=ps,
                sampled=is_sampled,
                validation=validation,
                etl_ok=row_etl_ok,
                blocking_reason=None if row_etl_ok else (
                    validation.detail if validation is not None else ps.reason
                ),
            )
        )

    prescreen_pass = sum(
        1 for ps in prescreen_results.values() if ps.verdict == PrescreenVerdict.PASS
    )
    prescreen_blocked = len(rows) - prescreen_pass

    summary = AdmissionSummary(
        total_rows=len(rows),
        prescreen_pass=prescreen_pass,
        prescreen_blocked=prescreen_blocked,
        sample_size=len(sampled_ids),
        validated=validated,
        validated_partial=validated_partial,
        validated_fail=validated_fail,
        etl_ok=etl_ok,
        etl_blocked=etl_blocked,
        blocked_by_reason=dict(sorted(blocked_by_reason.items())),
    )
    return decisions, summary


def manifest_to_jsonable(
    decisions: list[AdmissionDecision],
    summary: AdmissionSummary,
    *,
    issue: str,
    generated_at: str,
) -> dict[str, Any]:
    """Serialise decisions + summary into the manifest JSON shape."""
    return {
        "schema_version": "1.0",
        "issue": issue,
        "generated_at": generated_at,
        "summary": asdict(summary),
        "rows": [
            {
                "row_id": d.row_id,
                "source": d.source,
                "source_doi": d.source_doi,
                "prescreen": asdict(d.prescreen),
                "sampled": d.sampled,
                "validation": (
                    asdict(d.validation) if d.validation is not None else None
                ),
                "etl_ok": d.etl_ok,
                "blocking_reason": d.blocking_reason,
            }
            for d in decisions
        ],
    }


__all__ = [
    "DOI_REGEX",
    "PLACEHOLDER_REGISTRANTS",
    "PLACEHOLDER_SOURCE_TOKENS",
    "PLACEHOLDER_SUFFIX_RE",
    "PLACEHOLDER_SUFFIX_TOKENS",
    "AdmissionDecision",
    "AdmissionSummary",
    "DOIMetadata",
    "DOISecondarySourceBackend",
    "InMemoryBackend",
    "PrescreenResult",
    "PrescreenVerdict",
    "StagingRow",
    "ValidationResult",
    "ValidationVerdict",
    "build_admission_manifest",
    "cross_validate",
    "manifest_to_jsonable",
    "prescreen_doi",
    "select_sample",
]
