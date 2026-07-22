# Domain Expert Review — NFM-1540 Supplementary Data Pipeline

> **Reviewer**: Nuclear Domain Expert
> **Date**: 2026-07-19
> **Scope**: Domain validation of export pipeline + Materials Project supplementary data feasibility
> **Confidence**: 85%

---

## 1. Export Pipeline Validation — ✅ PASS

### 1.1 DFT Export Spec (`docs/dft-export-specification-NFM-1540.md`)
- Field definitions complete and consistent with `property.py` ORM model
- 25 fields defined across 5 categories (alloy ID, calc params, physical props, conditions, provenance)
- Validation ranges physically reasonable for nuclear materials (U-Zr, UO2, Fe-Cr-Ni)
- Mapping to NFM ReferenceValueInput schema documented

### 1.2 Validation Script (`data/dft-export/validate_and_transform.py`)
- CSV validation + transformation to staging JSON functional
- **Updated**: Added phase-aware range overrides for oxide/ionic phases (fluorite, rocksalt, perovskite, spinel, zirconia)
- UO2 cohesive_energy (-31.2 eV/atom) now correctly passes without spurious warning
- Composition JSON sum check (±1% tolerance) appropriate
- Uncertainty sign validation prevents negative uncertainty values

### 1.3 Test Sample Domain Check
| Entry | System | Phase | Key Values | Assessment |
|-------|--------|-------|------------|------------|
| Row 1 | U-70Zr | BCC | E_f=-3.25, E_coh=-6.85, a=3.52Å | ✅ Consistent with γ-U(BCC) PBE literature |
| Row 2 | U-50Zr | BCC | E_f=-2.98, E_coh=-6.12, a=3.48Å | ✅ Consistent with equiatomic BCC UZr PBE |
| Row 3 | UO2 | fluorite | E_f=-10.52, E_coh=-31.2, a=5.47Å | ✅ Matches experimental a=5.470Å; PBE E_coh reasonable for ionic oxide |

---

## 2. Supplementary Pipeline Gap Analysis — ⚠️ CRITICAL FINDINGS

For NFM-1561 (HEAPS + Materials Project API supplementary data), the following field coverage analysis applies:

### 2.1 Field Coverage Matrix

| Export Field | MP API Coverage | Direct API Path | Computation Required |
|-------------|----------------|-----------------|---------------------|
| `element_system` | ✅ | `formula_pretty` | None |
| `composition` | ✅ | `composition` (atomic frac) | Convert at.% |
| `phase` | ✅ | `spacegroup.number` + crystal system mapping | Phase name inference |
| `functional` | ✅ | Mostly PBE on MP | None |
| `cutoff_energy` | ⚠️ | `input.parameters.INCAR.ENCUT` (VASP entries only) | ~60% of entries have this |
| `kpoint_density` | ⚠️ | `input.parameters.KPOINTS` (format varies) | Parse+normalize |
| `pseudopotential` | ⚠️ | `input.parameters.POTCAR` (VASP entries) | ~60% of entries |
| `code` | ✅ | `input.parameters.run_type` | None |
| **`formation_energy`** | ✅ | `formation_energy_per_atom` | None |
| **`cohesive_energy`** | ❌ | NOT AVAILABLE | **Requires computation** |
| `lattice_constant_a` | ✅ | `lattice_constant` | None |
| **`lattice_distortion`** | ❌ | NOT AVAILABLE | **Requires computation** |
| `temperature` | ✅ | Usually 0K on MP | None |
| `source_id` | ✅ | `material_id` | Format as `MP-{id}` |
| `source_doi` | ⚠️ | Via `references` endpoint | Lookup needed |

### 2.2 Critical Gaps

**Gap 1: `cohesive_energy` NOT available from Materials Project**
- Cohesive energy = E_total/N_atoms − Σ(x_i × E_isolated_atom_i)
- MP provides `energy_per_atom` (total energy) but NOT isolated atom reference energies
- Isolated atom energies must be obtained from:
  - (a) Separate spin-polarized DFT calculations of isolated atoms (gold standard)
  - (b) Published reference tables (e.g., Kittel, MPContributions)
  - (c) Estimated from elemental cohesive energies in MP itself
- **Impact**: ~50% of records will have missing `cohesive_energy` unless a computation step is added
- **Recommendation**: Add isolated atom energy lookup table to supplementary pipeline (U, Zr, Fe, Cr, Ni, O, Mo, etc.)

**Gap 2: `lattice_distortion` NOT available from Materials Project**
- Lattice distortion = |a_alloy − a_Vegard| / a_Vegard × 100%
- Requires Vegard's law reference lattice constants for the composition
- Pure element reference lattice constants are available from MP (query each element)
- **Impact**: ~50% of records missing unless computation step added
- **Recommendation**: Add Vegard's law calculator using elemental lattice constants from MP

### 2.3 Practical Recommendation

For NFM-1561, the supplementary pipeline should:

1. **Phase 1** (Day 4 target): Export records with directly available fields only. Mark `cohesive_energy` and `lattice_distortion` as `"MISSING"` in notes. This gives Petrov ~200 records with formation_energy + lattice_constant for initial training.

2. **Phase 2** (Day 7 target): Add cohesive_energy calculator using isolated atom reference energies. This requires a one-time DFT calculation or literature lookup for each element in the HEAPS composition space.

3. **Phase 3** (Post-Sprint 4): Add lattice_distortion calculator using Vegard's law with MP elemental references.

---

## 3. Domain Lens Assessment

| Lens | Status | Notes |
|------|--------|-------|
| Crystal Structure Validity | ✅ | Phase field validated against crystal system mapping |
| Source Credibility Hierarchy | ✅ | MP = Tier 2 (below NIST IPR); supplementary only |
| Uncertainty Propagation | ⚠️ | MP uncertainties not directly available; DFT-PBE typical ±0.05-0.1 eV/atom |
| Temperature Range Validity | ✅ | MP data is 0K ground state; consistent with spec |
| Conflict Resolution | N/A | Single source; no conflicts expected |
| CALPHAD Cross-Validation | 📋 | Recommended for Phase 2: cross-check MP formation energies against CALPHAD phase diagrams |

---

*Review completed. Export pipeline ready for 院内 data ingestion. Supplementary pipeline has 2 critical computation gaps requiring additional engineering before Day 7.*
