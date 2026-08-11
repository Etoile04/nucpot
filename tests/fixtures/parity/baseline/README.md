# V2 Parity Test Reference Baseline

Curated, deterministic, human-validated fixture set that the V2 parity test
([NFM-2891](/NFM/issues/NFM-2891)) compares V2 extraction output against
instead of running the V1 stub mode. Built per the decision recorded in
[NFM-2890](/NFM/issues/NFM-2890) "CTO option 2": staging has no LLM keys,
so V1 stub mode returns canned data; we compare V2 against a deterministic
baseline.

Upstream context:
- [NFM-2891](/NFM/issues/NFM-2891) — Create reference baseline for V2 parity test (integration task)
- [NFM-2890](/NFM/issues/NFM-2890) — Configure staging LLM keys OR define stub-mode parity test scope
- [NFM-2875](/NFM/issues/NFM-2875) — V2 extraction pipeline reviews/follow-ups

## Layout

Each fixture is a self-contained directory with three files:

```
tests/fixtures/parity/baseline/<fixture-id>/
  source.txt       # plain-text source (factual reference content)
  expected.json    # V2 stub-mode extraction output (final chunks)
  metadata.json    # citation, validator, validation date, ontology_version pinned
```

`expected.json` is the canonical V2 orchestrator output: a JSON array of
`chunk_type="final"` `ExtractionChunk` records (see
`apps/api/src/nfm_db/services/extraction/types.py`). Each record carries
the in-memory dataclass fields (`content`, `chunk_type`, `_source_span`,
`metadata`, `parent_chunk_id`). The `metadata` block embeds the entities
extracted by `EntityExtractor` (formulas, properties, measurements) plus
the per-chunk `summary` stamped by `ChunkBuilder`.

## Fixtures

| Fixture | Material | Property domains exercised |
| --- | --- | --- |
| `uo2-fcc-lattice/` | Stoichiometric UO₂ (fluorite, Fm-3m) | `lattice_constant`, `density`, `bulk_modulus`, `melting_point` |
| `mox-thermal-conductivity/` | (U,Pu)O₂ MOX fuel, Pu=0.08 | `thermal_conductivity`, `specific_heat`, `density` |
| `thoria-mixed-oxide/` | ThO₂ (fluorite, Fm-3m) | `lattice_parameter`, `density`, `bulk_modulus`, `shear_modulus`, `youngs_modulus`, `formation_energy` |
| `zircaloy-cladding-modulus/` | Zircaloy-4 (PWR cladding) | `youngs_modulus`, `shear_modulus`, `bulk_modulus`, `density`, `melting_point` |

## Selection Rationale

Fixtures were chosen against three criteria (§3 of the design rationale;
[NFM-2890](/NFM/issues/NFM-2890)):

1. **Material coverage** — at least one fluorite actinide dioxide
   (UO₂, ThO₂), one mixed-oxide fuel (MOX), and one non-oxide structural
   alloy (Zircaloy-4). Each exercise spans 4-6 distinct properties so
   the regex-driven property extractor sees realistic vocabulary variety.
2. **Property-vocabulary coverage** — the union of properties across
   fixtures touches every entry in the V2
   `EntityExtractor._PROPERTY_NAMES` set that is operationally relevant
   for nuclear fuel materials (lattice constant/parameter, density,
   melting point, bulk/shear/youngs modulus, thermal conductivity,
   specific heat, formation energy).
3. **Formula regex coverage** — fluorites (UO₂, (U₁₋ₓPuₓ)O₂, ThO₂),
   space-group symbols (Fm-3m), alloying elements (Zr, Sn, Fe, Cr),
   and acronyms (DFT, DSC, FCC, LDA) are all represented so the
   `EntityExtractor._FORMULA_RE` regex is exercised under non-trivial
   token patterns.

Each fixture's `metadata.json` lists the source sentence that supports
every entity/relation/property claimed in `expected.json`, satisfying the
"every entity/relation/property is traceable to a quoted source sentence"
acceptance criterion. Inline `[p.X]` markers are not used; the
`source_sentence` field in `metadata.json` carries the citation.

## Reproducibility

The V2 pipeline (RawTextLoader → SectionSegmenter → EntityExtractor →
PropertyNormalizer → ChunkBuilder) is fully deterministic on its
inputs — no LLM calls, no DB access, no clock-dependent logic. The
acceptance criterion "5 successive V2 extractions of source.txt produce
byte-identical intermediate outputs" is therefore equivalent to "5
successive runs of the pipeline regenerate byte-identical
`expected.json`".

The reproducible helper lives at
`tools/parity_baseline/compute_expected.py`. It mirrors the exact
regex/normalization rules from
`apps/api/src/nfm_db/services/extraction/steps/{raw_text_loader,section_segmenter,entity_extractor,property_normalizer,chunk_builder}.py`
(no project imports) so any consumer — CI, local developer, integration
task — can regenerate `expected.json` offline.

Verify with:

```bash
python3 tools/parity_baseline/compute_expected.py \
  tests/fixtures/parity/baseline/<fixture-id>/source.txt \
  /tmp/check.json
diff /tmp/check.json tests/fixtures/parity/baseline/<fixture-id>/expected.json
```

A clean diff confirms the pipeline still emits the contract this baseline
claims.

> **Note.** Because `RawTextLoader` collapses all whitespace runs into a
> single space (a design choice in the V2 step; see NFM-2677 B2), the
> "sections" detected by `SectionSegmenter` collapse to a single chunk
> per fixture. The expected output therefore contains exactly one final
> chunk per fixture today.

## What This Baseline Catches

- **Regex drift.** Changes to
  `_FORMULA_RE`, `_PROPERTY_NAMES`, `_MEASUREMENT_RE`,
  `_PROPERTY_ALIASES`, or `_UNIT_ALIASES` flip a fixture's
  `summary.{formula_count,property_count,measurement_count}` away
  from the pinned values in `metadata.json`. CI fails before the
  contract change merges.
- **Normalizer drift.** A change to `PropertyNormalizer` that drops an
  alias (or adds one) shifts the emitted `properties[]` array;
  baseline diffs catch this even when the count is unchanged.
- **Section-segmenter / loader drift.** Whitespace handling changes
  in `RawTextLoader` move `_source_span` boundaries or collapse
  section splits; byte-identity of `expected.json` catches both.
- **Chunk schema drift.** Adding/removing/renaming fields on
  `ExtractionChunk` (e.g. swapping `_source_span` for `source_span`)
  surfaces as a JSON-key diff.

## What This Baseline Does NOT Catch

- **LLM extraction drift.** The pipeline exercised here is the
  pure-regex path; once LLM-backed steps are wired in
  (post-NFM-2868), this baseline does not validate prompt/output
  format. A separate ontology-extraction baseline is required for
  that surface area and is out of scope for NFM-2892.
- **Quality-gate / staging drift.** The pipeline emits chunks; what
  happens after (quality gate, dedup, gap scan, ORM staging) is
  covered by [NFM-2875](/NFM/issues/NFM-2875) follow-ups, not here.
- **Cross-language / cross-encoder drift.** All sources are English
  monocase-ASCII. Chinese-language or mixed-script sources (a real
  concern for OntoFuel) need a separate baseline seeded from the
  LangChain/Chinese-translation pipeline (NFM-2696 / NFM-2011 work).
- **Long-doc chunking.** The fixture corpus is intentionally short
  (one page per source). The `_CHUNK_MAX_CHARS = 20_000` chunker in
  `extraction_pipeline.py` is not exercised; that surface area is
  covered by the V1 legacy behavior captured in
  `test_parity_flag_routing.py`.
- **Database schema drift on `extraction_chunks.source_span`.**
  The orchestrator persists chunks as
  `{"start_offset": int, "end_offset": int}` JSONB on the
  `extraction_chunks` row (see `extraction_orchestrator_v2.py`). The
  baseline covers the in-memory chunk contract; a DB column-shape
  regression must be caught by the migration tests.

## Six-Step Update Procedure

When the V2 pipeline intentionally changes in a way that flips a fixture
output (e.g. a new property alias is added, a unit alias is added), do
NOT edit `expected.json` by hand. Follow these six steps so the change
is reviewable and traceable:

1. **Confirm the change is intentional.** Open the upstream PR that
   modifies
   `apps/api/src/nfm_db/services/extraction/steps/*.py`. Capture the
   issue/PR number and the affected fixture(s).
2. **Regenerate locally.** For each affected fixture, run:
   ```bash
   python3 tools/parity_baseline/compute_expected.py \
     tests/fixtures/parity/baseline/<fixture>/source.txt \
     tests/fixtures/parity/baseline/<fixture>/expected.json
   ```
   Confirm the diff against the previously-committed `expected.json`
   matches your intent (e.g. exactly one new property in the new
   `properties[]` array).
3. **Update `metadata.json`.** Bump `ontology_version` if the
   change is driven by an ontology bump (review
   `git log` for `OntologyVersion` changes since the previous
   pinned value); update `validator.validation_method` to reference
   the PR; update `validation_date` to today (UTC).
4. **Re-validate traceability.** Walk every entity/relation/property
   in the new `expected.json` and add or update a matching
   `values_sources[*].source_sentence` entry in `citation` of
   `metadata.json`. The acceptance bullet
   "every entity/relation/property is traceable to a quoted source
   sentence" is non-negotiable.
5. **Run the 5x reproducibility check.** For each affected fixture,
   execute the helper 5 times into a temp path and confirm 5 unique
   hashes after `sort -u | wc -l` returns 1. Pasted output of the
   5x run + the diff-step screenshot (or `diff -u` paste) belongs in
   the commit message so reviewers can see the contract is held.
6. **Open a PR with the diff co-located.** Commit both the source
   change and the baseline update in the SAME PR (so the contract
   lands as a unit), reference this directory's path in the PR body,
   and route to NFM-2891 (integration) + Code Reviewer.

## Limitations

- **One chunk per fixture today.** Because `RawTextLoader` flattens
  whitespace before `SectionSegmenter` runs, the markdown headings in
  `source.txt` are not section boundaries in the post-step-1 content
  the segmenter sees. The single-chunk output is not a bug — it's a
  behavior inherited from NFM-2677 B2 — but it does mean this
  baseline does not yet exercise multi-section fan-out. NFM-2694
  follow-up (post-NFM-2868) should add at least one multi-section
  fixture or refactor `RawTextLoader` to preserve paragraph
  boundaries.
- **Ontology version pinned at "0.1.0".** That value is the initial
  semver per [NFM-2580](https://example.invalid/NFM-2580) and matches
  what `_INITIAL_VERSION` declares in
  `apps/api/src/nfm_db/api/v1/ontology_version.py` for the moment
  before any published ontology row exists. Once a real `published`
  ontology is published in the DB, [NFM-2891](/NFM/issues/NFM-2891)
  should query the latest published version at test time rather than
  trust the pinned string.
- **English-only sources.** As above; Chinese-language baselines are
  out of scope here.
- **No figure / table extraction.** Multimodal_extraction
  (`extract_figures`, `extract_tables`) is exercised by
  `test_parity_flag_routing.py`, not here.

## Updating the Helper

`tools/parity_baseline/compute_expected.py` mirrors the V2 step
modules by copy. If those modules gain new steps, new fields, or
new normalization rules, update the helper in lockstep so the
on-disk baseline continues to reflect the live pipeline. The
helper's three regex tuples and the `_UNIT_ALIASES` /
`_PROPERTY_ALIASES` dicts are the authoritative copy targets —
keep them in sync with their upstream module.
