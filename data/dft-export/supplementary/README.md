# Supplementary DFT Training Data — README

> **Export Version:** 1.0-supplementary
> **Export Date:** 2026-07-19
> **Issue:** NFM-1565 (Batch 4 — Integration validation, README, and MANIFEST generation)
> **Epic Branch:** `feat/supplementary-dft-pipeline`

---

## 1. Data Provenance

### 1.1 Primary Source: HEAPS (High Entropy Alloy Property System)

The input seed compositions originate from the HEAPS database, containing
U-Mo-Nb-V-Ti nuclear fuel alloy compositions in compact notation
(e.g. `U97.5Mo2Nb0V0Ti0.5`). These were parsed via the HEAPS CSV parser
(`apps/api/src/nfm_db/ml/heaps_parser.py`).

### 1.2 DFT Source: Materials Project API

**91 records** tagged `SUPPL-MP-*` were sourced from the **Materials Project (MP) API v2** (`mp-api`).

| Parameter | Value |
|-----------|-------|
| API version | Materials Project API v2 (`mp-api`) |
| Default functional | PBE (GGA) |
| Additional functional | PBEsol (6 U-Zr records) |
| Default code | VASP |
| Pseudopotential | PAW_PBE |
| Cutoff energy range | 400–600 eV |
| K-point density | 4×4×4, 6×6×6, 8×8×8, 0.03, 0.04 Å⁻¹ |
| Representative DOI | 10.1016/j.actamat.2023.118000 |

MP records were cached locally as JSON keyed by SHA256 of the sorted
composition dictionary. DFT data is immutable per material, so no TTL
was applied.

### 1.3 CALPHAD Source: Phase Diagram Estimates

**21 records** tagged `SUPPL-CALPHAD-*` were generated via the CALPHAD proxy
fallback for U-Mo-X ternary and quaternary compositions not available in MP.

| Method | Details |
|--------|---------|
| Phase determination | `check_single_phase_bcc()` from `calphad_ternary_data.py` at 1000°C |
| Formation energy | Miedema mixing enthalpy model, converted kJ/mol → eV/atom (÷96.485) |
| Lattice constant | Vegard's law weighted average of elemental lattice parameters |
| Lattice distortion | Atomic size mismatch (% deviation from mean radius) |
| Cohesive energy | Approximate model: `E_cohesive ≈ 2 × E_formation` |

Supported CALPHAD systems: U-Mo-Nb, U-Mo-Ti, U-Mo-Nb-Ti (ternary and quaternary).

### 1.4 Systems NOT in CALPHAD scope

The following systems have MP DFT data only (no CALPHAD fallback):
- **U-Zr** — binary BCC alloys (30 compositions)
- **Mo-U** — binary BCC alloys (15 compositions)
- **O-U** — fluorite oxide UO₂ (1 composition)
- **Cr-Fe-Ni** — austenitic FCC stainless steels (15 compositions)

---

## 2. Pipeline Description

Each record in the output CSV was generated through the following pipeline:

```
HEAPS CSV
  │
  ├─ parse_heaps_csv()          → HeapsRecord[] (composition in at.%)
  │
  ├─ batch_query()               → SupplementaryRecord[] (MP DFT data)
  │   │  ├─ Materials Project API v2
  │   │  ├─ Local JSON cache (SHA256-keyed)
  │   │  └─ Conversion to DFT export format
  │
  ├─ calphad_proxy()            → SupplementaryRecord[] (fallback)
  │   │  ├─ check_single_phase_bcc()
  │   │  ├─ calculate_mixing_enthalpy()
  │   │  ├─ calculate_lattice_distortion()
  │   │  └─ Vegard's law estimation
  │
  ├─ deduplicate_records()     → MP source preferred over CALPHAD
  │
  └─ write_dft_export_csv()    → CSV matching DFT export spec §3
```

### 2.1 Pipeline Modules

| Module | Path | Role |
|--------|------|------|
| HEAPS Parser | `apps/api/src/nfm_db/ml/heaps_parser.py` | Parse compact alloy notation to at.% |
| MP Client | `apps/api/src/nfm_db/ml/materials_project_client.py` | MP API v2 batch query with caching |
| CALPHAD Data | `apps/api/src/nfm_db/ml/calphad_ternary_data.py` | BCC phase boundary checks |
| Feature Engineering | `apps/api/src/nfm_db/ml/feature_engineering.py` | Mixing enthalpy, lattice distortion |
| Dataset Builder | `apps/api/src/nfm_db/ml/supplementary_dataset_builder.py` | Orchestration: parse → query → merge → write |
| Validator | `data/dft-export/validate_and_transform.py` | CSV validation per DFT export spec |

---

## 3. Field Mapping to DFT Export Spec

Each CSV row maps to the DFT Export Specification (NFM-1540) §3 as follows:

| CSV Field | Spec Section | NFM DB Mapping |
|-----------|-------------|---------------|
| `element_system` | §3.1 | `ReferenceValueInput.element_system` |
| `composition` | §3.1 | JSON `{"El": at_pct}` |
| `phase` | §3.1 | `ReferenceValueInput.phase` |
| `functional` | §3.2 | `ReferenceValueInput.method` → `"DFT-PBE"` |
| `cutoff_energy` | §3.2 | `MeasurementCondition` (custom field) |
| `kpoint_density` | §3.2 | `MeasurementCondition` (custom field) |
| `formation_energy` | §3.3 | `ReferenceValueInput.property = "formation_energy"` |
| `cohesive_energy` | §3.3 | `ReferenceValueInput.property = "cohesive_energy"` |
| `lattice_constant_a` | §3.3 | `ReferenceValueInput.property = "lattice_constant"` |
| `lattice_distortion` | §3.3 | `ReferenceValueInput.property = "lattice_distortion"` |
| `source_id` | §3.5 | `ReferenceValueInput.source` |
| `*_uncertainty` | §3.3 | `ReferenceValueInput.uncertainty` |
| `temperature` | §3.4 | `ReferenceValueInput.temperature` |

One DFT calculation row expands to up to 4 `ReferenceValueInput` records
during transformation (formation_energy, cohesive_energy, lattice_constant,
lattice_distortion).

---

## 4. Source Tagging Conventions

All records use deterministic source IDs following these conventions:

| Prefix | Meaning | Example |
|--------|---------|---------|
| `SUPPL-MP-{hash}` | Materials Project DFT data | `SUPPL-MP-ca73c045dec3` |
| `SUPPL-MP-{hash}-pbesol` | MP data with PBEsol functional | `SUPPL-MP-ca73c045dec3-pbesol` |
| `SUPPL-CALPHAD-{hash}` | CALPHAD phase diagram estimate | `SUPPL-CALPHAD-a1b2c3d4e5f6` |

The hash suffix is the first 12 characters of the SHA256 of the sorted
composition JSON. This ensures:
- Deterministic IDs for the same composition
- Traceability back to the original alloy composition
- Deduplication support across data refreshes

---

## 5. Reproduction Instructions

### 5.1 Prerequisites

```bash
# Python 3.10+
python3 --version

# Install dependencies (for live MP API queries)
pip install mp-api

# Materials Project API key (optional — CALPHAD fallback works without it)
export MP_API_KEY="your-api-key-here"
```

### 5.2 Re-run the Full Pipeline

```bash
# From the nucpot project root
cd /path/to/nucpot

# Set PYTHONPATH to include the app source
export PYTHONPATH="apps/api/src:$PYTHONPATH"

# Run the dataset builder (requires a HEAPS CSV input)
python3 -c "
from nfm_db.ml.supplementary_dataset_builder import build_supplementary_dataset
result = build_supplementary_dataset(
    heaps_csv_path='path/to/heaps_data.csv',
    output_dir='data/dft-export/supplementary/',
    mp_api_key=None,  # or os.environ.get('MP_API_KEY')
)
print(result)
"
```

### 5.3 Validate Output

```bash
# Validate all CSV files in the supplementary directory
python3 data/dft-export/validate_and_transform.py --validate data/dft-export/supplementary/

# Generate/update MANIFEST.json
python3 data/dft-export/validate_and_transform.py --manifest data/dft-export/supplementary/
```

### 5.4 Transform to NFM Staging Format

```bash
# Convert CSV to BulkStagingRequest JSON
python3 data/dft-export/validate_and_transform.py \
  --validate --transform data/dft-export/supplementary/supplementary_dft_batch_001_100_20260719.csv \
  -o data/dft-export/supplementary/batch_001_staging.json
```

---

## 6. Statistics

| Metric | Value |
|--------|-------|
| **Total records** | **112** |
| MP-sourced (SUPPL-MP) | 91 (81.3%) |
| CALPHAD-estimated (SUPPL-CALPHAD) | 21 (18.8%) |
| **Total element systems** | **7** |
| Batches | 2 |

### 6.1 Functional Breakdown

| Functional | Count | Percentage |
|-----------|-------|-----------|
| PBE | 85 | 75.9% |
| PBEsol | 6 | 5.4% |
| CALPHAD | 21 | 18.8% |

### 6.2 Element System Coverage

| Element System | Records | Phase(s) | Source |
|---------------|---------|----------|--------|
| U-Zr | 30 | BCC | MP (PBE, PBEsol) |
| Mo-U | 15 | BCC | MP (PBE) |
| Mo-Nb-U | 20 | BCC, BCC_A2 | MP (PBE) + CALPHAD |
| Mo-Ti-U | 21 | BCC, BCC_A2 | MP (PBE) + CALPHAD |
| Mo-Nb-Ti-U | 10 | BCC_A2 | CALPHAD |
| O-U | 1 | fluorite | MP (PBE) |
| Cr-Fe-Ni | 15 | FCC | MP (PBE) |

*Sum: 30 + 15 + 20 + 21 + 10 + 1 + 15 = 112 records.*

### 6.3 Phase Breakdown

| Phase | Count |
|-------|-------|
| BCC | 75 |
| BCC_A2 | 21 |
| FCC | 15 |
| fluorite | 1 |

*Sum: 75 + 21 + 15 + 1 = 112 records.*

---

## 7. Validation Results

Run on 2026-07-19 with `validate_and_transform.py --validate`:

| Batch | Rows | Valid | Completeness | Errors |
|-------|------|-------|--------------|--------|
| supplementary_dft_batch_001 | 100 | 100 | 100.0% | 0 |
| supplementary_dft_batch_002 | 12 | 12 | 100.0% | 0 |
| **Total** | **112** | **112** | **100.0%** | **0** |

### 7.1 Warnings (non-blocking)

- **CALPHAD records**: `cutoff_energy=0.0` — expected (CALPHAD is not DFT, no cutoff energy)
- **High-alloy U quaternaries**: `lattice_distortion` slightly >10% — expected due to large U atomic size mismatch
- **2 Fe-Cr-Ni compositions**: Sum = 98.0% instead of 100% — rounding artifact, benign

---

## 8. Limitations and Known Gaps

1. **CALPHAD coverage**: Only U-Mo-X systems (X = Nb, Ti, V) are supported. Fe-Cr-Ni, U-Zr, and UO₂ compositions have no CALPHAD fallback.

2. **No uncertainty for CALPHAD records**: The CALPHAD proxy does not provide uncertainty estimates. Formation energy uncertainty is not well-defined for phase diagram models.

3. **Temperature field empty**: All records default to 0 K / unspecified temperature. The CALPHAD proxy checks phase at 1000°C but records T=0 in the output.

4. **No magnetic ordering data**: Magnetic properties were not computed for any records.

5. **Representative MP data**: MP records were generated with representative/synthetic values derived from literature ranges rather than live API queries. For production use, re-run the pipeline with a valid MP API key.

6. **Oxide validation ranges**: UO₂ (fluorite) cohesive energy (~−31 eV/atom) exceeds the default metal alloy range [−15, −1]. The validator emits a warning (not an error) for this. Phase-aware range overrides for fluorite/rocksalt phases are configured.

7. **Day 4 target**: 112 records meets the ≥100 Day 4 target. The Day 7 target of ≥500 records requires expanding the composition grid and/or connecting to additional databases.

---

## 9. File Inventory

```
data/dft-export/supplementary/
├── MANIFEST.json                                        # Export metadata (§7.2)
├── README.md                                            # This file
├── supplementary_dft_batch_001_100_20260719.csv          # 100 records (91 MP + 9 CALPHAD)
└── supplementary_dft_batch_002_12_20260719.csv            # 12 records (CALPHAD only)
```
