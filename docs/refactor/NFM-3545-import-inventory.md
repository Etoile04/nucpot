# NFM-3545 / D1 — Inventory of every internal import of `gap_scan_service`

> **Issue:** NFM-3561 — `[NFM-3545-D1] Inventory every internal import of gap_scan_service`
> **Parent:** NFM-3545 — `[NFM-2868-P1-3] Delete legacy gap_scan_service.py after parity with gap_scanner.py`
> **Author:** Lead Engineer (NFM-3561, run `c4d45adc-…`)
> **Source of truth:** `rg gap_scan_service apps/` (verified 2026-08-24)
> **Status:** D1 deliverable — handed off to D2 (parity fixture) and D3 (migration)

This document is the machine-checkable inventory required by NFM-3545 D1. It
catalogues every Python source file that imports the **legacy** `gap_scan_service`
module (`apps/api/src/nfm_db/services/gap_scan_service.py`) so that D3 can plan
a one-shot migration with no runtime breakage.

---

## 1. Module under inventory

| Property | Value |
|----------|-------|
| Module path | `apps/api/src/nfm_db/services/gap_scan_service.py` |
| Lines of code | 286 |
| Module docstring marker | `.. deprecated:: NFM-2620` (lines 10–12) |
| Deprecation target (per module docstring) | `nfm_db.services.coverage_scan_service.CoverageScanService` |
| `DeprecationWarning` emitted | Yes, in `GapScanService.__init__` (lines 146–151) — message text: *"GapScanService is deprecated. Use CoverageScanService from coverage_scan_service instead."* |
| Public class | `GapScanService` |
| Public dataclasses | `GapTuple`, `CoverageStats`, `SystemCoverage`, `ScanResult`, `StagingCounts` |
| Private helpers referenced by tests | `_compute_priority`, `_parse_staging_counts` |
| Underlying storage | `nfm_db.models.ref_gap_fill.RefGapFillStaging` / `StagingStatus` |

### 1.1 Is the legacy module shadowed / re-exported from `gap_scanner.py`?

**No.** There is **no DeprecationWarning shim** in either direction.

Verified facts:

| Direction | Question | Answer |
|-----------|----------|--------|
| `gap_scanner.py` → `gap_scan_service.py` | Does `gap_scanner.py` re-export / import anything from `gap_scan_service`? | **No.** `rg 'from nfm_db.services.gap_scan_service' apps/api/src/nfm_db/services/gap_scanner.py` → 0 hits. |
| `gap_scan_service.py` → `gap_scanner.py` | Does the legacy module import anything from `gap_scanner`? | **No.** The legacy module has no `from nfm_db.services.gap_scanner` import. |
| Cross-reference | Do both modules share a class name? | Yes — both define `GapScanService`, but they cover **different domains** (see §1.2). They are not interchangeable. |

**Where the deprecation message actually points.** The legacy module's
`__init__` (line 146–151) tells callers to migrate to
`coverage_scan_service.CoverageScanService`. That module, in turn, imports
*two helpers* from `gap_scanner.py` (`coverage_scan_service.py:31-34`):

```python
from nfm_db.services.gap_scanner import (
    extract_entity_types,
    iter_property_names,
)
```

So the real migration target chain is:

```
legacy: gap_scan_service.GapScanService       (RefGapFillStaging-based, hardcoded 12 tuples)
   ↓ (deprecation message in __init__)
new:    coverage_scan_service.CoverageScanService   (DB-record coverage, ontology-driven)
   ↓ (depends on)
helper: gap_scanner.{extract_entity_types, iter_property_names}   (shared ontology parsing)
```

### 1.2 Domain separation between the three modules

| Module | Domain | Storage | Class | Notes |
|--------|--------|---------|-------|-------|
| `gap_scan_service.py` | Reference-data gap scan (hardcoded 12 target tuples) | `RefGapFillStaging` | `GapScanService` (legacy) | Marked deprecated NFM-2620 |
| `gap_scanner.py` | Extraction-chunk gap scan + recall + coverage (NFM-2586 / NFM-2575) | `ExtractionGap`, `ExtractionChunk`, `ExtractionJob`, `OntologyVersion` | `GapScanService` (new — ExtractionGap domain) | Has `__all__` exporting `CoverageMetrics`, `GapScanResult`, `GapScanService`, `RecallMetrics`, `compute_recall`, `extract_entity_types`, `iter_property_names` |
| `coverage_scan_service.py` | DB-record coverage analysis (NFM-2620) | `DataCollectionRequest`, `OntologyVersion` (+ Material/Potential/Property) | `CoverageScanService` | Reuses `gap_scanner.{extract_entity_types, iter_property_names}` |

**Implication for D2/D3:** The parent epic's title — *"Delete legacy gap_scan_service.py after parity with gap_scanner.py"* — is **directionally accurate but technically imprecise**. There is no symbol-for-symbol parity between the two modules. The two `GapScanService` classes solve different problems. D3 will need to:

1. Decide whether the legacy `GapScanService` callers can be **completely replaced** by `coverage_scan_service.CoverageScanService` (the documented deprecation path), or whether they need direct migration to `gap_scanner.GapScanService`.
2. Verify whether the **legacy 12-tuple target table** (`_DEFAULT_TARGET_TUPLES` at lines 32–45) has any operator-visible surface that the new modules do not cover.

---

## 2. Import inventory

All Python files referencing `gap_scan_service` (verified via
`rg -n 'gap_scan_service' apps/` on 2026-08-24 against `main`@`67e20fee1`):

### 2.1 Production code

| # | File | Line | Import statement | Symbols consumed | Import kind |
|---|------|------|------------------|------------------|-------------|
| 1 | `apps/api/src/nfm_db/services/extraction_orchestrator.py` | 33 | `from nfm_db.services.gap_scan_service import GapScanService` | `GapScanService` | `from X import Y` |
| 2 | `apps/api/src/nfm_db/api/v1/reference_gaps.py` | 29 | `from nfm_db.services.gap_scan_service import GapScanService` | `GapScanService` | `from X import Y` |

### 2.2 Tests

| # | File | Line | Import statement | Symbols consumed | Import kind |
|---|------|------|------------------|------------------|-------------|
| 3 | `apps/api/tests/test_gap_scan_service.py` | 15–23 | `from nfm_db.services.gap_scan_service import (`<br>`    CoverageStats,`<br>`    GapScanService,`<br>`    GapTuple,`<br>`    ScanResult,`<br>`    StagingCounts,`<br>`    SystemCoverage,`<br>`    _compute_priority,`<br>`    _parse_staging_counts,`<br>`)` | All 8 public + helper symbols | `from X import (Y, Z, …)` |
| 4 | `apps/api/tests/services/test_coverage_scan_service.py` | 33 | `from nfm_db.services.gap_scan_service import GapScanService` | `GapScanService` (used to assert the `DeprecationWarning`) | `from X import Y` |
| 5 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1399 | `from nfm_db.services.gap_scan_service import CoverageStats` | `CoverageStats` (used inside the `_make_scan_result` mock factory) | inline `from X import Y` inside a test helper |
| 6 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1429 | `from nfm_db.services.gap_scan_service import GapTuple` | `GapTuple` (used inside `test_gap_scan_wrapper_success`) | inline `from X import Y` inside a test |
| 7 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1446 | `"nfm_db.services.gap_scan_service.GapScanService.scan_gaps"` | `GapScanService.scan_gaps` | `unittest.mock.patch` target string |
| 8 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1483 | `"nfm_db.services.gap_scan_service.GapScanService.scan_gaps"` | `GapScanService.scan_gaps` | `unittest.mock.patch` target string |
| 9 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1600 | `"nfm_db.services.gap_scan_service.GapScanService.scan_gaps"` | `GapScanService.scan_gaps` | `unittest.mock.patch` target string |
| 10 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1688 | `"nfm_db.services.gap_scan_service.GapScanService.scan_gaps"` | `GapScanService.scan_gaps` | `unittest.mock.patch` target string |
| 11 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1819 | `"nfm_db.services.gap_scan_service.GapScanService.scan_gaps"` | `GapScanService.scan_gaps` | `unittest.mock.patch` target string |
| 12 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1871 | `"nfm_db.services.gap_scan_service.GapScanService.scan_gaps"` | `GapScanService.scan_gaps` | `unittest.mock.patch` target string (note: indented one extra level — inside an inner `with` block) |
| 13 | `apps/api/tests/services/test_extraction_orchestrator.py` | 1985 | `"nfm_db.services.gap_scan_service.GapScanService.scan_gaps"` | `GapScanService.scan_gaps` | `unittest.mock.patch` target string |

### 2.3 Non-import references (must update during migration but are not import sites)

| # | File | Line | Reference | Notes |
|---|------|------|-----------|-------|
| N1 | `apps/api/tests/services/test_coverage_scan_service.py` | 288 | `async def test_gap_scan_service_emits_deprecation(db_session):` | Test **asserts** the legacy `DeprecationWarning` and that the warning message mentions `CoverageScanService`. **D3 decision:** keep the test (still meaningful) OR delete it once the legacy module is gone. |
| N2 | `apps/api/tests/test_gap_scan_service.py` | (whole file) | The whole file is dedicated to testing the legacy module. | Will be deleted alongside the legacy module. |

### 2.4 Documentation / prose references (out-of-scope for D3 code edits, but useful context)

| File | Lines | Note |
|------|-------|------|
| `docs/architecture/nucpot-technical-architecture-2026-08-07.md` | 252, 645 | Architectural critique of `gap_scan_service.py:27-40` (hardcoded 12 target tuples) and `gap_scan_service.py:25-40, 144-153` (hardcoded target + faulty covered query, "缺口 C5"). |

### 2.5 Outside `apps/` — wired-in scripts and CI

**No matches.** Searched:

- `rg gap_scan_service scripts/` → 0 hits
- `rg gap_scan_service apps/api/src/nfm_db/cli/` → 0 hits
- `grep gap_scan_service pyproject.toml` → 0 hits
- `rg -n gap_scan_service .github/` → 0 hits
- `rg --files-with-matches 'gap_scan_service' . --glob '!apps/' --glob '!.git/'` → 1 hit (`docs/architecture/nucpot-technical-architecture-2026-08-07.md`, prose only)

There are **no** scripts or CI entrypoints that import `gap_scan_service`. The only out-of-`apps/` reference is the architectural doc above (prose, not an import site).

---

## 3. Symbol-by-symbol cross-reference for D2 (parity fixture)

D2 needs to verify that every public symbol of the legacy module has an
equivalent (or replaceable) counterpart in the migration target. The
following table is the parity-fixture seed.

| Legacy symbol (in `gap_scan_service.py`) | Consumed by (per §2) | Migration candidate | D2 verification notes |
|------------------------------------------|----------------------|---------------------|------------------------|
| `GapScanService` | #1 (production), #2 (production), #3 (test), #7–13 (mock targets), N1 (test name) | `coverage_scan_service.CoverageScanService` (per deprecation message) OR `gap_scanner.GapScanService` (per parent epic title) | D2 must pick the canonical target and update **all 8 call sites + the 7 mock targets + the 1 test name** in one migration. |
| `CoverageStats` | #3 (test), #5 (inline test helper) | None (legacy-specific) | If `CoverageScanService` is the target, the mock factory in `test_extraction_orchestrator.py:_make_scan_result` will need to construct whatever result type the new service returns. |
| `GapTuple` | #3 (test), #6 (inline test) | None (legacy-specific) | Same as `CoverageStats` — mock-side only; D3 rewrites the helper or deletes the test. |
| `ScanResult` | #3 (test) | None | Mock-side only. |
| `StagingCounts` | #3 (test) | None | Mock-side only. |
| `SystemCoverage` | #3 (test) | None | Mock-side only. |
| `_compute_priority` (private) | #3 (test) | None (private helper) | Tests only — deleted with the legacy module. |
| `_parse_staging_counts` (private) | #3 (test) | None (private helper) | Tests only — deleted with the legacy module. |

**Production-only consumers (the ones D2 cannot wave away):**

- `apps/api/src/nfm_db/services/extraction_orchestrator.py:33` — imports `GapScanService`. This is the *only* consumer that runs in the production request path. The orchestrator calls `GapScanService.scan_gaps(...)` and records the gaps as an `ExtractionStep` (per the docstring on lines 1425–1431 "AC: Wrapper calls GapScanService.scan_gaps and records on step").
- `apps/api/src/nfm_db/api/v1/reference_gaps.py:29` — imports `GapScanService` for the `GET /api/reference-gaps` and `POST /api/reference-gaps/scan` endpoints (per `gap_scan_service.py:7-8` "Design reference: NFM-54 Section 2.1, Section 2.3").

D2 must therefore verify that the migration target exposes an equivalent of `scan_gaps()` (returning gap tuples that the orchestrator and the HTTP route can consume) before D3 edits any source.

---

## 4. What D3 will need to touch

A migration that deletes `gap_scan_service.py` and re-points all callers must edit **at minimum**:

| File | Lines | Edit type |
|------|-------|-----------|
| `apps/api/src/nfm_db/services/extraction_orchestrator.py` | 33 | Change import to migration target |
| `apps/api/src/nfm_db/api/v1/reference_gaps.py` | 29 | Change import to migration target |
| `apps/api/tests/test_gap_scan_service.py` | (whole file, 1+) | Delete (legacy-only test file) |
| `apps/api/tests/services/test_coverage_scan_service.py` | 33, 288 (function name) | Delete import; delete `test_gap_scan_service_emits_deprecation` (no longer meaningful) |
| `apps/api/tests/services/test_extraction_orchestrator.py` | 1399, 1429, 1446, 1483, 1600, 1688, 1819, 1871, 1985 | Rewrite the 2 inline imports + update 7 `patch(...)` target strings |
| `apps/api/src/nfm_db/services/gap_scan_service.py` | (whole file) | Delete |
| `docs/architecture/nucpot-technical-architecture-2026-08-07.md` | 252, 645 | Refresh the architectural critique (point to the migration target, not the deleted file) |

**Total source-line edits:** 9 in 5 files + 1 file deletion + 1 doc refresh.

---

## 5. How to reproduce this inventory

```bash
# 1. From repo root.
# 2. Confirm branch + tip:
git branch --show-current
git log -1 --oneline

# 3. Find every Python import site of the legacy module:
rg -n 'gap_scan_service' apps/

# 4. Cross-check no out-of-apps imports exist:
rg --files-with-matches 'gap_scan_service' . --glob '!apps/' --glob '!.git/'

# 5. Confirm the deprecation shim status (should print zero hits):
rg -n 'from nfm_db.services.gap_scan_service' apps/api/src/nfm_db/services/gap_scanner.py
rg -n 'from nfm_db.services.gap_scanner' apps/api/src/nfm_db/services/gap_scan_service.py
```

If the commands above produce any new hit not listed in §2, this inventory is stale and must be re-run before D3 begins edits.

---

## 6. Open questions for D2

1. **Migration target confirmation.** The legacy module's deprecation message
   points to `coverage_scan_service.CoverageScanService`; the parent epic's
   title points to `gap_scanner.GapScanService`. They are not equivalent.
   D2 must reconcile this before D3 can pick a single replacement symbol.
2. **`_DEFAULT_TARGET_TUPLES` (12 hardcoded tuples).** The legacy module's
   target table is hardcoded in `gap_scan_service.py:32-45`. Does the
   migration target accept an equivalent `target_tuples` parameter? If not,
   the 12 tuples will be silently lost on migration.
3. **`scan_gaps(element_systems=...)` filter parameter.** The legacy
   method accepts an `element_systems` filter. D2 must verify the
   replacement exposes the same filter signature, otherwise
   `apps/api/src/nfm_db/services/extraction_orchestrator.py` will need a
   wider behavioural change than a single import edit.
4. **`StagingStatus`/`RefGapFillStaging` coupling.** The legacy module
   reads `RefGapFillStaging` directly to compute the *covered* set. D2 must
   verify whether the replacement reads the same table (it likely does not,
   since `coverage_scan_service` reads `DataCollectionRequest` instead).

---

*Generated 2026-08-24 by Lead Engineer for NFM-3561.*
