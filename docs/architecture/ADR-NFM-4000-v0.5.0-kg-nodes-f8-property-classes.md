# ADR-NFM-4000 — v0.5.0 `kg_nodes.label`: Add 6 F8 Property classes

- **Status:** proposed (2026-09-01) — pending `[PREREG-APPROVED]` per CTO §3.5
- **Issue:** [NFM-4000](/NFM/issues/NFM-4000)
- **Parents:**
  - [NFM-3424](/NFM/issues/NFM-3424) (closed) — Expand NDE heuristic extractor scope
  - [NFM-3887](/NFM/issues/NFM-3887) — Layer 1-3 analysis (NDE-FOLLOWUP-2)
  - [NFM-3885](/NFM/issues/NFM-3885) — LLM→bridge→kg_nodes 26→9 gap
  - [NFM-3901](/NFM/issues/NFM-3901) — heuristic dedup ValueError fix
- **Architectural constraint (CTO):** NFM-3986 — v0.5.0 ontology decision,
  not an inline engineering fix.

## Context

NFM-3424 re-extraction against source `9320cb50-eb65-4178-8d2e-c56aeb848b21`
(Owen et al. 2023, *Diffusion in undoped and Cr-doped amorphous UO2*)
achieved **8/8 by `(property_name, value)` pairs across surfaces** but
**kg_nodes strict surface remained 2/8**. The 6 missing-on-kg_nodes items
ARE extracted and persisted to staging — the mapper chain drops them at the
kg_nodes surface because `property_types` (the upstream gate enforced by
`extraction_to_db_mapper._lookup_property_type`) lacks 6 new Property
class entries.

The structural gap is at the **`property_types` table**, seeded by
migration `031_seed_property_types.py`. Although `kg_nodes.label` is a
free-form `VARCHAR(500)`, items only reach `kg_nodes` if their
`property_name` first resolves to a `PropertyType` row. Items that fail
this lookup are silently skipped (mapper line 666-673,
`skipped_unknown_properties`).

Per CTO's explicit architectural constraint in NFM-3986, the missing
property classes are a **v0.5.0 ontology decision** affecting the
canonical `property_types` taxonomy, not an inline mapper patch.

## Decision

Add 6 new F8 Property classes to the canonical `property_types` taxonomy,
mapped to new `kg_nodes.label` entries with bilingual labels (中/英).
These classes are added in a **new** Alembic migration
(`065_add_v050_f8_property_types.py`) so the v0.5.0 boundary is explicit
and reversible.

### The 6 new F8 Property classes

| # | F8 Checkpoint | `property_name` (slug) | `kg_nodes.label` (zh) | `kg_nodes.label` (en) | Family | Unit | Description |
|---|---|---|---|---|---|---|---|
| 1 | F8 #3 | `cr_doped_activation_energy` | 铬掺杂扩散激活能 | Cr-doped activation energy | energy | eV | Diffusion activation energy for Cr-containing UO2 |
| 2 | F8 #4 | `cr_doped_diffusion_coefficient` | 铬掺杂扩散系数 | Cr-doped diffusion coefficient (D0) | diffusivity | cm²/s | Pre-exponential factor D0 for Cr-doped UO2 |
| 3 | F8 #5 | `density_amorphous` | 非晶密度 | Amorphous density | density | g/cm³ | Amorphous-phase density |
| 4 | F8 #6 | `density_doped` | 掺杂密度 | Doped density | density | g/cm³ | Density of doped variants (e.g. 10 at% Cr-doped UO2) |
| 5 | F8 #7 | `rdf_peak_distance` | RDF峰位距离 | RDF peak distance | length | Å | Pair-distribution function peak position |
| 6 | F8 #8 | `bond_length` | 键长 | Bond length | length | Å | Bond length between specific atom pairs (e.g. Cr-O) |

### Required supplementary rows

To preserve the existing 2/8 strict regression guard, the seed must also
include **`activation_energy`** (F8 #1, undoped Ea). The current seed
does not include this row, so the constraint listed in NFM-4000
("Do NOT regress existing 2/8 strict checkpoints (undoped Ea, undoped
D0)") cannot be evaluated against the canonical seed today. Adding it
here restores the documented regression baseline; the migration is
idempotent (`ON CONFLICT (category_id, slug) DO NOTHING`) so a parallel
fix in another migration is safe.

### Mapper chain updates

1. **`kg_to_staging_bridge.py`** — add 6 new entries to `_PROPERTY_SLUGS`
   for the bilingual Chinese→English label mapping at lines 55-72.
2. **`property_mapping.json`** — add 6 new English→Chinese aliases for
   the new property classes (lines 13-18 are the existing reference).
3. **`heuristic_extractor.py`** — extend `_PROPERTY_RULES` to emit the
   new disambiguated labels when the surrounding prose indicates doping
   or phase context (additive rules appended after line 231 per the
   NFM-3517 AC-A5 regression guard).
4. **`extraction_to_db_mapper.ONTOFUEL_CATEGORY_TO_SLUG`** — no change
   required; the 6 new rows are added under the `physical` category
   slug (consistent with `density` and `diffusion_coefficient`).

### Family → PropertyCategory mapping (heuristic_extractor.FAMILY_TO_CATEGORY)

The new entries inherit the existing family mapping:

| Family | PropertyCategory (Literal) | New entries |
|---|---|---|
| `energy` | `diffusion` | `cr_doped_activation_energy` |
| `diffusivity` | `diffusion` | `cr_doped_diffusion_coefficient` |
| `density` | `physical` | `density_amorphous`, `density_doped` |
| `length` | `physical` | `rdf_peak_distance`, `bond_length` |

No `FAMILY_TO_CATEGORY` change is required.

## Acceptance Criteria mapping

| AC | Implementation | Test |
|---|---|---|
| AC-1 | Migration 065 seeds 6 new `property_types` rows | `test_seed_v050_f8_property_types` |
| AC-2 | Mapper chain (`literature_service.py` → `extraction_to_db_mapper` → `kg_to_staging_bridge`) emits new `kg_nodes.label` for items matching new `property_name` patterns | `test_literature_service.F8V050PropertyClasses` |
| AC-3 | Re-running re-extraction of source 9320cb50 against staging achieves kg_nodes strict **8/8** | `test_kg_nodes_strict_8_of_8` |
| AC-4 | Worker log has zero `"label not in taxonomy"` / `"mapper dropped"` errors | grep on worker log + `test_literature_service.NoMapperDrops` |
| AC-5 | This ADR | (self) |

## Pre-registration (CTO §3.5)

Per the issue's research methodology constraint, this ADR is the
**analysis plan** that must receive `[PREREG-APPROVED]` from NDE review
BEFORE running confirmatory extraction comparison. The plan above is
the preregistered specification; deviations during implementation must
be re-preregistered.

## References

* **NFM-3887 Layer 1-3 analysis** — root cause for the silent-zero
  dropping at the kg_nodes surface; mapper chain layers identified.
* **NFM-3885 LLM→bridge→kg_nodes 26→9 gap** — investigation report
  quantifying the missing items per surface.
* **NFM-3901 heuristic dedup ValueError fix** — established that
  heuristic items DO reach `extraction_results`; the drop is downstream
  of the dedup gate, at `property_types` lookup.
* **NFM-3517** — NDE F8 scorecard heuristic pattern classes
  (`rdf_peak`, `bond_length`); these classes exist in the heuristic
  extractor but are NOT seeded as `property_types`, hence the gap.
* **NFM-3424** — parent issue (closed 2026-09-01 with scorecard evidence).

## Change log

* **2026-09-01 (NFM-4000)** — proposed; awaiting `[PREREG-APPROVED]`.
