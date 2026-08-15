# Architecture Document Archive

This directory contains historical versions of the nucpot technical architecture document.

| File | Date | Status | Notes |
|------|------|--------|-------|
| `20260815/nucpot-technical-architecture-2026-08-07.md` | 2026-08-07 → archived 2026-08-15 | v1.0 | Original audit. **Prerequisite reading** — still the most complete description of gaps C1-C7 and Phases 0-6. Superseded by v2.0 for current state. |
| `20260718/technical-roadmap-nuclear-fuel-data-platform.md` | 2026-07-18 | v0.x | Earlier roadmap. v1.6 lives in `docs/`. |

## Why v2.0 exists separately (not a rewrite)

v2.0 (`docs/architecture/nucpot-technical-architecture-2026-08-15.md`) is the **current** document. v1.0 was archived rather than deleted because:

1. The 7 gaps (C1-C7) and Phase 0-6 plan are still the *foundational* narrative — Phase 2-6 planning hasn't changed, only the "done" status has.
2. The detailed code index, chunking strategy comparison, and decision rationale (D1-D4) are richer than the v2.0 summary.
3. Cross-references in ADRs (e.g. ADR-NFM-2737, ADR-NFM-2739) and PR descriptions still reference v1.0 terminology.

**Read both if you want full context.** v1.0 = the *why*; v2.0 = the *what is now* + *how it's deployed*.
