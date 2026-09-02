# ADR-NFM-4000 — F8 Property classes: orthogonal decomposition, not variant-in-label

| Field | Value |
| --- | --- |
| **Status** | Accepted (documents shipped code) |
| **Date** | 2026-09-01 |
| **Author** | CTO |
| **Source issue** | [NFM-4000](/NFM/issues/NFM-4000) (cancelled — proposal superseded) |
| **Implemented by** | [NFM-4036](/NFM/issues/NFM-4036) (done), [NFM-4037](/NFM/issues/NFM-4037) (done) |
| **Investigation chain** | [NFM-3885](/NFM/issues/NFM-3885), [NFM-3887](/NFM/issues/NFM-3887), [NFM-3901](/NFM/issues/NFM-3901), [NFM-3424](/NFM/issues/NFM-3424) |
| **Live successor** | [NFM-4058](/NFM/issues/NFM-4058) (in_progress) |

## 1. Context

Re-extraction of source `9320cb50-eb65-4178-8d2e-c56aeb848b21` (Owen et al. 2023,
*Diffusion in undoped and Cr-doped amorphous UO2*) reached **8/8 on the
`property_name`+`value` cross-surface scorecard but only 2/8 on the strict
`kg_nodes` surface**. The six missing checkpoints were extracted and persisted to
staging; they were dropped between the KG and the `kg_nodes` surface.

[NFM-3887](/NFM/issues/NFM-3887) traced this to the bridge mapper chain. Two
distinct root causes were conflated under one symptom:

1. **Property-label coverage.** The Owen2023 corpus emits Chinese property names
   (`扩散激活能`, `Cr掺杂扩散前指数因子`, `RDF峰`, `键长`, …) that
   `kg_to_staging_bridge._PROPERTY_SLUGS` did not map, so `_slugify` produced
   non-canonical slugs.
2. **Material-label coverage.** The bridge passed Material `KGNode` labels through
   unchanged, so `amorphous UO2` did not satisfy the F8 scorecard's
   `element_system IN ('UO2', 'UO2+Cr', 'U-Cr-O')` predicate.

[NFM-4000](/NFM/issues/NFM-4000) proposed to fix both by adding **six new
variant-specific `kg_nodes.label` enum values**:
`cr_doped_activation_energy`, `cr_doped_diffusion_coefficient`,
`density_amorphous`, `density_doped`, `rdf_peak_distance`, `bond_length`.

That proposal was **rejected and the issue cancelled**. This ADR records why, so
the same proposal is not re-derived the next time the 2/8 symptom appears.

## 2. Decision

> **Property class and material variant are orthogonal dimensions. The property
> slug encodes only the physical quantity. Doping state and morphology are
> carried by `element_system`, never by the property label.**

Concretely:

- **`_PROPERTY_SLUGS` is many-to-one onto base physical quantities.** Every
  source-language spelling of a quantity — *including doped variants* — collapses
  onto one base slug.
- **`_canonical_element_system` carries the variant dimension.** Morphology and
  doping adjectives are stripped from the Material label, and the residual
  chemistry is bucketed into the scorecard's three accepted values.

### Why variant-in-label was rejected

1. **Cartesian explosion.** The property enum would grow as
   *quantity × dopant state × morphology*. Six checkpoints needed six new enums;
   the next corpus (undoped/doped × amorphous/crystalline/nano × 20 quantities)
   would need dozens. The base-slug set stays O(quantities).
2. **It makes the variant unqueryable.** `density_amorphous` and `density_doped`
   cannot be compared to `density` without string surgery. "All densities for
   UO2+Cr" becomes a `LIKE` scan instead of an equality predicate on
   `element_system`.
3. **It forces fragile proximity heuristics.** Deciding between
   `activation_energy` and `cr_doped_activation_energy` requires guessing from
   surrounding prose whether the *value* is the doped one. The NFM-4000 attempt
   hit exactly this: a 120-character prefix window captured `"Cr-doped samples,"`
   from three lines above and mislabelled the **undoped** 0.30 eV Ea, which
   regressed one of the two already-passing checkpoints. The fix attempted was to
   shrink the window to 60 characters — a magic number tuned to one document.
   Under the orthogonal design the question never arises: the quantity is
   `activation_energy` either way, and the dopant state comes from the Material
   node, which is structured data rather than a prose guess.
4. **It duplicates a dimension the schema already models.** `element_system`
   exists and the F8 scorecard already predicates on it.

## 3. Implementation as shipped

All line references are `apps/api/src/nfm_db/services/kg_to_staging_bridge.py`
on `origin/main`.

**Base property slugs** — [NFM-4036](/NFM/issues/NFM-4036), commits `4ddb0a196`,
`f3638f50c`:

| Source labels | Base slug | Lines |
| --- | --- | --- |
| `扩散激活能`, `扩散活化能`, `活化能`, `激活能`, `氧扩散激活能`, **`Cr掺杂扩散激活能`** | `activation_energy` | 90–95 |
| `扩散指前因子`, `预指数因子`, `氧扩散指前因子`, **`Cr掺杂扩散前指数因子`** | `pre_exponential_factor` | 100–106 |
| `扩散系数` | `diffusion_coefficient` | 108 |
| `密度` | `density` | 111 |
| `RDF峰` | `rdf_peak` | 113 |
| `键长` | `bond_length` | 114 |

The two bolded rows are the load-bearing evidence: a **Cr-doped** source label
maps to the **base** slug. No `cr_doped_*` slug exists anywhere in the mapping.

Note also line 108: `diffusion_coefficient` is deliberately kept distinct from
`pre_exponential_factor`. D and D₀ are different quantities; that split is a
*quantity* distinction and therefore correctly belongs in the property enum,
unlike the doping distinction.

**Variant normalization** — [NFM-4037](/NFM/issues/NFM-4037), commit `4726b22d7`:

- `_ADJECTIVE_PREFIXES` (lines 155–162): `"undoped and Cr-doped "`, `"Cr-doped "`,
  `"amorphous "`, `"crystalline "`, `"polycrystalline "`, `"nano-"`. Compound
  prefixes precede their single-word counterparts so the longest match wins.
- `_canonical_element_system` (line 169) buckets the stripped chemistry:
  `Cr` present **and** `UO2` present → `UO2+Cr`; `UO2` alone → `UO2`; a
  Cr-uranium-oxide label without `UO2` → `U-Cr-O`.
- Dopant detection reads `has_cr` from the **pre-strip** label (line 199),
  because `"Cr-doped"` is itself a stripped adjective and would otherwise erase
  the dopant before bucketing.
- Applied at both bridge call sites: lines 394 and 496.

Every NFM-4000 checkpoint is reachable as a (slug, element_system) pair:

| NFM-4000 proposed enum | Shipped representation |
| --- | --- |
| `cr_doped_activation_energy` | `activation_energy` × `UO2+Cr` |
| `cr_doped_diffusion_coefficient` | `pre_exponential_factor` × `UO2+Cr` |
| `density_amorphous` | `density` × `UO2` (morphology stripped) |
| `density_doped` | `density` × `UO2+Cr` |
| `rdf_peak_distance` | `rdf_peak` |
| `bond_length` | `bond_length` |

## 4. Consequences

### Positive

- Property enum growth is bounded by the number of physical quantities.
- Doped/undoped and amorphous/crystalline comparisons are equality predicates on
  `element_system`.
- The undoped-Ea regression guard holds structurally rather than by a tuned
  prose window.
- Adding a morphology or dopant only touches `_ADJECTIVE_PREFIXES` plus a bucket
  rule — no property-enum migration, no re-labelling of stored rows.

### Negative / accepted costs

- **Morphology is lossy at the `element_system` layer.** `amorphous UO2` and
  `crystalline UO2` both normalize to `UO2`. This is deliberate: the F8 scorecard
  predicates on chemistry. If a consumer needs to distinguish phases, the correct
  fix is a **separate structured field** (e.g. a `morphology` / phase column) —
  **not** re-encoding morphology into the property slug or the element system.
  Anyone tempted to add `density_amorphous` should add that field instead.
- **`_ADJECTIVE_PREFIXES` is prefix-anchored and ordered.** A label carrying the
  adjective in a non-prefix position (`"UO2, amorphous"`) is not stripped. New
  corpora must be checked against this assumption.
- Bucketing is corpus-informed: Cr-bearing UO2 maps to `UO2+Cr` because Owen2023
  describes a UO2 matrix plus Cr dopant, never the bare ternary. A genuine
  U-Cr-O ternary corpus needs the bucket rule revisited.

## 5. Status of the original acceptance criteria

| AC | Disposition |
| --- | --- |
| AC-1 six new enum values | **Rejected by design.** Base slugs + `element_system` supersede it (§2, §3). |
| AC-2 mapper chain emits new labels | **Satisfied differently** — NFM-4036 slugs + NFM-4037 normalization, both merged. |
| AC-3 `kg_nodes` strict 8/8 | **Owned by [NFM-4058](/NFM/issues/NFM-4058)**, which formalizes `_extract_f8_table_rows` + `heuristic_f8` into `origin/main`. Not claimed here. |
| AC-4 zero mapper-drop errors | Follows AC-3; verified under NFM-4058. |
| AC-5 document the decision | **This ADR.** NFM-4000 guessed ADR-009 as the home; ADR-009 is cancelled-blocker wedge reconciliation, an unrelated topic. |

AC-3 and AC-4 are deliberately **not** marked satisfied. The runtime scorecard
confirmation was deferred behind a `[PREREG-APPROVED]` research-methodology gate
on NFM-4000 and never ran there; NFM-4058 is the live owner.

## 6. Anti-pattern for future readers

If you see **"extracted to staging but `kg_nodes` strict surface is short"**:

1. Check whether the *quantity* is missing from `_PROPERTY_SLUGS` — add the
   source spelling, mapped to an **existing base slug** where one fits.
2. Check whether the *material label* fails `_canonical_element_system` — extend
   `_ADJECTIVE_PREFIXES` or the bucket rules.
3. **Do not** create a property slug that embeds doping state, morphology, phase,
   temperature, or any other sample attribute. That is this ADR's rejected
   alternative.
