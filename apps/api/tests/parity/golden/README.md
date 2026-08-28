# Parity Golden Inputs (NFM-3581)

These JSON files are the **input fixtures** for the V1-hardcoded vs V2-ontology-only
snapshot diff harness. Each file represents the ontology payload that would be
fed to `build_ontology_extraction_prompt(ontology_version)` and (reconstructed)
`build_v1_legacy_prompt(ontology_data)`.

## Why these files

The diff harness must run identical input through both prompt paths and compare
the rendered prompt strings. The golden set covers ≥5 representative scenarios
to ensure the comparison catches real semantic differences, not just incidental
whitespace changes.

## Scenarios

| # | File | Scenario | What it stresses |
|---|------|----------|------------------|
| 1 | `01_short_paper.json` | Short single paper | Smallest meaningful input; baseline sanity check |
| 2 | `02_long_paper.json` | Long single paper | Exceeds ONTOLOGY_CONTEXT_BUDGET_CHARS; triggers V2 truncation warning |
| 3 | `03_multi_doc.json` | Multi-document | Two fuel systems side-by-side (PWR + HTGR); distinct entities per paper |
| 4 | `04_table_heavy.json` | Table-heavy | 25+ tabular properties on one entity; tests sort/dedup at scale |
| 5 | `05_special_chars.json` | Special characters | CJK, math symbols (×, ⁻, ²), Greek (α, β, γ), units (Ω/cm, W/m·K) |

## Shape

Each file is a JSON object that mirrors the structure of `OntologyVersion.ontology_data`:

- `property_categories`: list of `{name, standard_properties}` (NFM-3004 0.2.0+ schema)
- `entity_types`: list of `{name, description, required_properties}`
- `relation_types`: list of `{name, source_types, target_types, description}`

Files starting with `_` (e.g., `_description`, `_scenario`, `_scenario_notes`)
are harness metadata and are NOT part of the ontology payload.

## Adding new scenarios

Append `0N_<slug>.json` with a new `_scenario` value, an entry in the table above,
and a brief description. New scenarios should exercise a different code path in
`_build_ontology_context_block` or `_build_ontology_categories_block` — e.g.,
empty entity_types, very large relations, nested unicode escape sequences.