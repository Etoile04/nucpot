# V1-hardcoded vs V2-ontology-only — Snapshot Diff Report

_Generated: 2026-08-23 19:11:16 UTC_
_Inputs: 5 golden fixture(s)_

## Summary

| Status | Count |
|--------|-------|
| PASS | 0 |
| WARN | 5 |
| FAIL | 0 |

| Severity | Count |
|----------|-------|
| NONE | 0 |
| COSMETIC | 0 |
| NON_COSMETIC | 5 |
| BLOCKING | 0 |

## Per-Input Verdict

| Input | Scenario | Status | Severity | Categories Δ | Properties Δ | Comment Δ lines | V1 len | V2 len |
|-------|----------|--------|----------|--------------|---------------|-------------------|--------|--------|
| `01_short_paper` | short | **WARN** | NON_COSMETIC | +1/-11 | +2/-74 | 107 | 2667 | 2257 |
| `02_long_paper` | long | **WARN** | NON_COSMETIC | +10/-11 | +13/-74 | 172 | 2667 | 4340 |
| `03_multi_doc` | multi_doc | **WARN** | NON_COSMETIC | +4/-11 | +7/-74 | 130 | 2667 | 2805 |
| `04_table_heavy` | table_heavy | **WARN** | NON_COSMETIC | +1/-11 | +26/-74 | 126 | 2667 | 3025 |
| `05_special_chars` | special_chars | **WARN** | NON_COSMETIC | +3/-11 | +8/-72 | 123 | 2667 | 2675 |

## Per-Input Detail

### `01_short_paper` — short

**Scenario:** Single paper, 1 entity type, 1 relation, 2 required properties. Shortest possible meaningful prompt path input.

**Verdict:** WARN (NON_COSMETIC)

**Deltas:**

- V1 hardcoded 74 Chinese-alias property name(s) V2 dropped (e.g., `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`...). Expected post-NFM-3258: V2 sources canonical English names from the ontology rather than the hardcoded Chinese alias table.
- V2 added 2 canonical English property name(s) not in V1's hardcoded list (e.g., `density`, `thermal_conductivity`).
- category removed in V2: 其他性能
- category removed in V2: 密度
- category removed in V2: 弹塑性模型
- category removed in V2: 材料规格/组织信息
- category removed in V2: 比热容
- category removed in V2: 热传导率
- category removed in V2: 热膨胀
- category removed in V2: 硬化性能
- category removed in V2: 腐蚀
- category removed in V2: 辐照肿胀
- category removed in V2: 辐照蠕变
- category added in V2: NuclearFuel

**Notes:**

- comment text differs semantically (non-cosmetic)

**Categories only in V1 (11):** `其他性能`, `密度`, `弹塑性模型`, `材料规格/组织信息`, `比热容`, `热传导率`, `热膨胀`, `硬化性能`, `腐蚀`, `辐照肿胀`…

**Categories only in V2 (1):** `NuclearFuel`

**Properties only in V1 (74):** `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`, `体积模量`, `体积膨胀率`, `体膨胀系数`, `冷加工量`, `剪切模量`…

**Properties only in V2 (2):** `density`, `thermal_conductivity`

### `02_long_paper` — long

**Scenario:** Many entity types + verbose descriptions to exceed ONTOLOGY_CONTEXT_BUDGET_CHARS=8000. Verifies V2 truncation path while V1 just embeds everything.

**Verdict:** WARN (NON_COSMETIC)

**Deltas:**

- V1 hardcoded 74 Chinese-alias property name(s) V2 dropped (e.g., `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`...). Expected post-NFM-3258: V2 sources canonical English names from the ontology rather than the hardcoded Chinese alias table.
- V2 added 13 canonical English property name(s) not in V1's hardcoded list (e.g., `corrosion_rate`, `creep_rate`, `density`, `elastic_modulus`, `hydrogen_pickup`...).
- category removed in V2: 其他性能
- category removed in V2: 密度
- category removed in V2: 弹塑性模型
- category removed in V2: 材料规格/组织信息
- category removed in V2: 比热容
- category removed in V2: 热传导率
- category removed in V2: 热膨胀
- category removed in V2: 硬化性能
- category removed in V2: 腐蚀
- category removed in V2: 辐照肿胀
- category removed in V2: 辐照蠕变
- category added in V2: AusteniticSteel
- category added in V2: Beryllium
- category added in V2: FerriticSteel
- category added in V2: Graphite
- category added in V2: LeadBismuth
- category added in V2: MOXFuel
- category added in V2: MoltenSalt
- category added in V2: SiCComposite
- category added in V2: UO2Fuel
- category added in V2: ZrAlloyCladding

**Notes:**

- comment text differs semantically (non-cosmetic)

**Categories only in V1 (11):** `其他性能`, `密度`, `弹塑性模型`, `材料规格/组织信息`, `比热容`, `热传导率`, `热膨胀`, `硬化性能`, `腐蚀`, `辐照肿胀`…

**Categories only in V2 (10):** `AusteniticSteel`, `Beryllium`, `FerriticSteel`, `Graphite`, `LeadBismuth`, `MOXFuel`, `MoltenSalt`, `SiCComposite`, `UO2Fuel`, `ZrAlloyCladding`

**Properties only in V1 (74):** `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`, `体积模量`, `体积膨胀率`, `体膨胀系数`, `冷加工量`, `剪切模量`…

**Properties only in V2 (13):** `corrosion_rate`, `creep_rate`, `density`, `elastic_modulus`, `hydrogen_pickup`, `melting_point`, `specific_heat`, `swelling`, `thermal_conductivity`, `thermal_expansion`…

### `03_multi_doc` — multi_doc

**Scenario:** Two fuel systems side-by-side: PWR UO2/Zry-4 and HTGR UCO/TRISO. Different entity types per paper, shared relation vocabulary.

**Verdict:** WARN (NON_COSMETIC)

**Deltas:**

- V1 hardcoded 74 Chinese-alias property name(s) V2 dropped (e.g., `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`...). Expected post-NFM-3258: V2 sources canonical English names from the ontology rather than the hardcoded Chinese alias table.
- V2 added 7 canonical English property name(s) not in V1's hardcoded list (e.g., `corrosion_rate`, `density`, `oxide_thickness`, `swelling`, `thermal_conductivity`...).
- category removed in V2: 其他性能
- category removed in V2: 密度
- category removed in V2: 弹塑性模型
- category removed in V2: 材料规格/组织信息
- category removed in V2: 比热容
- category removed in V2: 热传导率
- category removed in V2: 热膨胀
- category removed in V2: 硬化性能
- category removed in V2: 腐蚀
- category removed in V2: 辐照肿胀
- category removed in V2: 辐照蠕变
- category added in V2: HTGR_Compact
- category added in V2: HTGR_TRISO
- category added in V2: PWR_Cladding
- category added in V2: PWR_FuelRod

**Notes:**

- comment text differs semantically (non-cosmetic)

**Categories only in V1 (11):** `其他性能`, `密度`, `弹塑性模型`, `材料规格/组织信息`, `比热容`, `热传导率`, `热膨胀`, `硬化性能`, `腐蚀`, `辐照肿胀`…

**Categories only in V2 (4):** `HTGR_Compact`, `HTGR_TRISO`, `PWR_Cladding`, `PWR_FuelRod`

**Properties only in V1 (74):** `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`, `体积模量`, `体积膨胀率`, `体膨胀系数`, `冷加工量`, `剪切模量`…

**Properties only in V2 (7):** `corrosion_rate`, `density`, `oxide_thickness`, `swelling`, `thermal_conductivity`, `yield_strength`, `young_modulus`

### `04_table_heavy` — table_heavy

**Scenario:** Single material (HT9 steel) with 25+ tabular properties (creep, swelling, yield, etc.). Tests both paths' sorting/dedup behavior.

**Verdict:** WARN (NON_COSMETIC)

**Deltas:**

- V1 hardcoded 74 Chinese-alias property name(s) V2 dropped (e.g., `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`...). Expected post-NFM-3258: V2 sources canonical English names from the ontology rather than the hardcoded Chinese alias table.
- V2 added 26 canonical English property name(s) not in V1's hardcoded list (e.g., `bulk_modulus`, `corrosion_rate`, `creep_rate_steady`, `creep_rate_transient`, `density`...).
- category removed in V2: 其他性能
- category removed in V2: 密度
- category removed in V2: 弹塑性模型
- category removed in V2: 材料规格/组织信息
- category removed in V2: 比热容
- category removed in V2: 热传导率
- category removed in V2: 热膨胀
- category removed in V2: 硬化性能
- category removed in V2: 腐蚀
- category removed in V2: 辐照肿胀
- category removed in V2: 辐照蠕变
- category added in V2: HT9_Steel

**Notes:**

- comment text differs semantically (non-cosmetic)

**Categories only in V1 (11):** `其他性能`, `密度`, `弹塑性模型`, `材料规格/组织信息`, `比热容`, `热传导率`, `热膨胀`, `硬化性能`, `腐蚀`, `辐照肿胀`…

**Categories only in V2 (1):** `HT9_Steel`

**Properties only in V1 (74):** `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`, `体积模量`, `体积膨胀率`, `体膨胀系数`, `冷加工量`, `剪切模量`…

**Properties only in V2 (26):** `bulk_modulus`, `corrosion_rate`, `creep_rate_steady`, `creep_rate_transient`, `density`, `dislocation_density`, `elongation`, `grain_size`, `hardness`, `inner_diameter`…

### `05_special_chars` — special_chars

**Scenario:** Property names include 密度、α相、β相、γ辐照、Ω/cm²、×10⁻⁶/K. Entity description has CJK + math + Greek. Verifies unicode normalization is consistent.

**Verdict:** WARN (NON_COSMETIC)

**Deltas:**

- V1 hardcoded 72 Chinese-alias property name(s) V2 dropped (e.g., `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`...). Expected post-NFM-3258: V2 sources canonical English names from the ontology rather than the hardcoded Chinese alias table.
- V2 added 8 canonical English property name(s) not in V1's hardcoded list (e.g., `ΔV/V（肿胀）`, `α→β转变温度`, `位错环密度 (m⁻²)`, `密度 (g/cm³)`, `热导率（W/m·K）`...).
- category removed in V2: 其他性能
- category removed in V2: 密度
- category removed in V2: 弹塑性模型
- category removed in V2: 材料规格/组织信息
- category removed in V2: 比热容
- category removed in V2: 热传导率
- category removed in V2: 热膨胀
- category removed in V2: 硬化性能
- category removed in V2: 腐蚀
- category removed in V2: 辐照肿胀
- category removed in V2: 辐照蠕变
- category added in V2: α相Zr合金
- category added in V2: β-SiC
- category added in V2: γ辐照硬化层

**Notes:**

- comment text differs semantically (non-cosmetic)

**Categories only in V1 (11):** `其他性能`, `密度`, `弹塑性模型`, `材料规格/组织信息`, `比热容`, `热传导率`, `热膨胀`, `硬化性能`, `腐蚀`, `辐照肿胀`…

**Categories only in V2 (3):** `α相Zr合金`, `β-SiC`, `γ辐照硬化层`

**Properties only in V1 (72):** `Johnson-Cook参数`, `Norton蠕变参数`, `Ramberg-Osgood参数`, `位错密度`, `位错硬化参数`, `体积模量`, `体积膨胀率`, `体膨胀系数`, `冷加工量`, `剪切模量`…

**Properties only in V2 (8):** `ΔV/V（肿胀）`, `α→β转变温度`, `位错环密度 (m⁻²)`, `密度 (g/cm³)`, `热导率（W/m·K）`, `热膨胀系数（×10⁻⁶/K）`, `电阻率`, `硬度增量 (ΔHV)`

## Methodology

Each golden input is fed to **two** prompt builders using the same `ontology_data` payload:

1. **V1-hardcoded** — `tests.parity.v1_legacy_prompt.build_v1_legacy_prompt()`. Reconstructs the pre-NFM-3258 behavior: 11 fixed `PropertyCategory` enum values + the hardcoded `STANDARD_PROPERTIES` mapping from `property_mapping.json`. Ignores `ontology_data` entirely (V1 never read it).

2. **V2-ontology-only** — `nfm_db.services.extraction_prompt.build_ontology_extraction_prompt()`. The current production path; sources categories and property names from `ontology_data` (`property_categories` 0.2.0+ schema, plus `entity_types[].required_properties`).

The comparator (`tests.parity.comparator`) extracts the **key set** of categories and standard property names from each rendered prompt, computes set differences, and emits a unified diff of the static prose blocks (everything outside the dynamic ontology/categories/names blocks).

The classifier (`tests.parity.diff_classifier`) applies the rules in the module docstring and emits a PASS / WARN / FAIL verdict with severity COSMETIC / NON_COSMETIC / BLOCKING.

## How to run

```bash
# Unit tests for the classifier:
pytest apps/api/tests/parity/test_diff_classifier.py -v

# Regenerate this report:
python -m tests.parity.harness
```
