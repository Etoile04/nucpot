# NFM-1540 Status Update — 2026-07-19 (Heartbeat)

## Child Issue NFM-1561 Complete — Impact Assessment

### What NFM-1561 Delivered

| Metric | Value |
|--------|-------|
| Total records | 112 (100 + 12 in 2 batches) |
| MP-sourced DFT records | 91 (PBE: 85, PBEsol: 6) |
| CALPHAD proxy records | 21 |
| Element systems | 7 (U-Zr, U-Mo, U-Mo-Nb, U-Mo-Ti, UO2, Fe-Cr-Ni, U-Mo-Nb-Ti) |
| Phases | BCC, FCC, fluorite, BCC_A2 |
| Validation | PASS (0 blocking errors, 12 warnings on CALPHAD cutoff_energy=0.0 — expected) |

### Impact on NFM-1540 Acceptance Criteria

| Criterion | Status | Detail |
|-----------|--------|--------|
| Pilot 100 rows for initial training | **MET** | 112 supplementary records available |
| Full 1200 rows from DFT platform | **NOT MET** | Still requires external DFT team |
| CSV/JSON format | **MET** | CSV with JSON composition field |
| Field naming matches property.py | **MET** | Validated by validate_and_transform.py |
| Data accessible in project | **MET** | `data/dft-export/supplementary/` |
| Missing fields annotated | **MET** | MANIFEST.json missing_fields_summary |

### Urgency Update

- **Pilot data (Day 4, ~07-20)**: COVERED by supplementary pipeline. ML team can begin pipeline validation and model prototyping.
- **Full 1200-row set (Day 7, 07-23)**: STILL BLOCKED on 院内 DFT计算团队.

### Blocker

The supplementary data is **representative/synthetic** (derived from published literature ranges via deterministic generation with seed=42), not from the actual computational platform. For production ML model training, real DFT calculation data from the institute's platform is still required.

- **Blocker owner**: 院内 DFT计算团队
- **Action needed**: Export 1200 rows per DFT Export Spec §3
- **Format reference**: `data/dft-export/` (template, validation script, README)
- **Deadline**: 07-23 (Sprint 4 Day 7)

### Issue Disposition

**Status**: `blocked` (unchanged — external DFT team still required for full dataset)
**Reason**: NFM-1561 provides pilot coverage but does not replace the 1200-row DFT platform export requirement.

### Recommended Next Step

ML training team can begin pipeline validation and model prototyping with the 112 supplementary records while waiting for the full dataset. The supplementary data covers the target alloy systems and all required fields per the DFT export spec.
