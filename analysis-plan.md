# [PREREG-APPROVED] SCF Parameter Investigation for 30 Non-Converged 54-Atom Cases

**Issue:** NFM-3381  
**Author:** Nuclear Domain Expert (NDE)  
**Date:** 2026-08-20  
**Status:** Pre-registered, pending data inspection  

---

## 1. Categorization of the 30 Non-Converged Cases

### Proposed Error Taxonomy

All 30 cases will be inspected via their `.out` files on `xingyi` and sorted into one of these categories:

| Category | Marker in `.out` file | Mechanism |
|---|---|---|
| **A. SCF oscillation** | `convergence NOT achieved` + energy oscillating (±0.01 eV over last 20 steps) | Charge-density mixing too aggressive or wrong initial magnetization |
| **B. SCF stagnation** | `convergence NOT achieved` + energy nearly flat (dE < 0.001 eV over last 50 steps) | Electron step too small, stuck in a flat region of the PES |
| **C. Cell/ionic divergence** | `convergence NOT achieved` + forces > 1 eV/Å or cell stress exploding | Structural instability at the current volume/magnetization |
| **D. Time-limit exit** | `convergence NOT achieved` + wall-time reached (no explicit error) | Simply ran out of the 12h budget |
| **E. k-point / symmetry crash** | `convergence NOT achieved` + IBZKPT error or symmetry reduction | Numerical noise in k-point mesh for defected supercells |

### Inspection Procedure

On `xingyi`, run:

```bash
# Step 1: Confirm the 30 non-converged cases
cd /path/to/54-atom/campaign
grep -rl "convergence NOT achieved" */OUTCAR */*.out 2>/dev/null | sort > /tmp/non_converged_30.txt
wc -l /tmp/non_converged_30.txt  # expect 30

# Step 2: For each case, extract SCF convergence tail for categorization
for f in $(cat /tmp/non_converged_30.txt); do
  d=$(dirname "$f")
  echo "=== $(basename "$d") ==="
  echo "SCF steps: $(grep -c 'DAV:\|RMM:' "$f" 2>/dev/null || echo 0)"
  echo "Final energy: $(grep 'energy without' "$f" 2>/dev/null | tail -1)"
  echo "Max force: $(grep 'FORCES: max' "$f" 2>/dev/null | tail -1)"
  echo "Wall clock: $(grep 'Elapsed time' "$f" 2>/dev/null | tail -1)"
  echo "EDIFF: $(grep 'EDIFF' "$d"/INCAR 2>/dev/null)"
  echo "ALGO: $(grep 'ALGO' "$d"/INCAR 2>/dev/null)"
  echo "AMIX: $(grep 'AMIX' "$d"/INCAR 2>/dev/null)"
  echo "BMIX: $(grep 'BMIX' "$d"/INCAR 2>/dev/null)"
  echo "NELM: $(grep 'NELM' "$d"/INCAR 2>/dev/null)"
  echo "ISIF: $(grep 'ISIF' "$d"/INCAR 2>/dev/null)"
  echo "MAGMOM: $(grep 'MAGMOM' "$d"/INCAR 2>/dev/null | head -1)"
  echo "KPOINTS mesh: $(head -1 "$d"/KPOINTS 2>/dev/null)"
  echo ""
done
```

### Expected Distribution (prior estimate)

Based on NDE experience with UO₂/PuO₂ 54-atom defect supercells at similar DFT+U settings:
- **Category A (oscillation):** ~40% (12 cases) — most common for high-defect-concentration supercells
- **Category B (stagnation):** ~20% (6 cases) — typically near-surface or interstitial defects
- **Category C (divergence):** ~10% (3 cases) — usually vacancy clusters or Frenkel pairs at extreme volumes
- **Category D (time-limit):** ~25% (8 cases) — charge sloshing in large defect dipoles
- **Category E (k-point crash):** ~5% (1-2 cases) — rare, symmetry-related

These priors will be updated after inspection.

---

## 2. Proposed Parameter Changes by Category

### Base Parameters (current campaign defaults — to be confirmed from INCAR files)

```
ENCUT = 500 eV
EDIFF = 1E-6
EDIFFG = -0.01
ISMEAR = 0 (Gaussian)
SIGMA = 0.05
LDAU = .TRUE.
LDAUTYPE = 2
LDAUU = 4.0  (U on 5f)
LDAUJ = 0.7
LDAUL = 3
ALGO = Normal
AMIX = 0.2
BMIX = 0.0001
ISYM = 0
```

### Category A: SCF Oscillation

**Root cause:** Charge-density mixing (Broyden `BMIX`) too aggressive for the defect-induced charge redistribution, or starting magnetization seeds a wrong local minimum.

**Proposed changes (applied sequentially, not all at once):**

| Parameter | Current | Proposed | Justification |
|---|---|---|---|
| `AMIX` | 0.2 | 0.1 | Reduce linear mixing fraction; standard fix for oscillatory SCF (Kresse & Furthmüller 1996, *Phys. Rev. B* 54, 11169) |
| `BMIX` | 0.0001 | 0.00005 | Halve Broyden mixing; conservative for 54-atom cells with defect dipoles |
| `MAGMOM` | per-atom | **AFM seed** (0.5 -0.5 0.5 -0.5 ...) | If current seed is FM, switch to collinear AFM ordering consistent with UO₂ ground state (Dorado et al. 2009, *Phys. Rev. B* 80, 014126) |
| `ICHARG` | 1 | 0 (if re-running from scratch) | Force charge-density reconstruction |

**Literature:** Kresse & Furthmüller, *Phys. Rev. B* **54**, 11169 (1996) — Section V discusses mixing parameter tuning for 3d/5f systems. Dorado et al., *Phys. Rev. B* **80**, 014126 (2009) — AFM ordering in UO₂ supercells.

### Category B: SCF Stagnation

**Root cause:** `NELM` (default 100) reached without convergence; the energy surface is flat near the current point.

**Proposed changes:**

| Parameter | Current | Proposed | Justification |
|---|---|---|---|
| `NELM` | 100 (default) | 300 | Allow more SCF steps; 54-atom defect cells commonly need 150-250 steps (NDE prior experience) |
| `ALGO` | Normal | All | All-band simultaneous update; more robust for flat PES regions (Kresse & Furthmüller 1996, Sec. IV.C) |
| `WEIMIN` | 0.2 (default) | 0.05 | Reduce electron step size for finer energy landscape traversal |

### Category C: Cell/Ionic Divergence

**Root cause:** The defect supercell is structurally unstable at the current volume, causing forces to diverge during ionic relaxation.

**Proposed changes:**

| Parameter | Current | Proposed | Justification |
|---|---|---|---|
| `EDIFFG` | -0.01 | -0.02 | Relax force convergence criterion; for defect properties energy convergence is more critical |
| `ISIF` | 3 (full relax) | 2 (fixed volume) | Hold volume fixed, let ionic positions relax; re-evaluate cell shape after convergence |
| `SMASS` | 0 | -3 (velocity Verlet) | Switch to damped MD to absorb structural instabilities |
| `POTIM` | 0 (default) | 0.015 | Small timestep for damped MD |

**Note:** If Category C cases don't converge with relaxed force criterion, the structure itself may be physically unreasonable. These should be flagged for exclusion rather than forced to converge.

### Category D: Time-Limit Exit

**Root cause:** SCF is progressing but too slowly to finish within 12h.

**Proposed changes:**

| Parameter | Current | Proposed | Justification |
|---|---|---|---|
| `ALGO` | Normal | Fast | Kerker preconditioner + residual minimization for faster early SCF convergence |
| `NCORE` | varies | 4 | Pin to 4 cores/band for better parallel scaling on xingyi nodes |
| `KPAR` | 1 | 4 | Parallel over k-points; 54-atom cells with 2×2×2 mesh benefit from k-parallelism |
| `LPLANE` | .TRUE. | .TRUE. (confirm) | Ensure plane-wise parallelization is on |

**Estimated speedup:** 2-3× wall-clock reduction.

### Category E: k-point / Symmetry Crash

**Root cause:** Numerical noise in the k-point mesh for highly defected supercells.

**Proposed changes:**

| Parameter | Current | Proposed | Justification |
|---|---|---|---|
| `KPOINTS` | Γ-centered 2×2×2 | Γ-centered 3×3×3 | Denser mesh resolves numerical instabilities; cost increase ~3.4× (27 vs 8 k-points) |
| `ISYM` | 0 | 0 (confirm) | Symmetry already off — confirm this wasn't accidentally re-enabled |

---

## 3. Acceptance Criterion for Convergence on Re-run

### Current Heuristic

The current campaign uses a `JOB DONE` string match. This is fragile.

### Proposed 4-Gate Convergence Criterion

A re-run is **converged** if and **only if** all of the following are satisfied:

1. **Electronic convergence:** `.out` contains `reached required accuracy` or final SCF step shows `energy change < EDIFF` (≤ 1E-6)
2. **Ionic convergence (if ISIF≥2):** Final `FORCES: max` < `EDIFFG` threshold
3. **No divergence markers:** Absence of `convergence NOT achieved`, `WARNING: Sub-Space-Matrix is not hermitian`, or `EDDDAV: Call to ZHEGV failed`
4. **`JOB DONE`** present in the final 10 lines (secondary confirmation)

### Detection Script

```bash
is_converged() {
  local out=$1
  grep -q "reached required accuracy" "$out" || return 1
  grep -q "FORCES: max" "$out" || return 1
  grep -q "convergence NOT achieved\|Sub-Space-Matrix\|ZHEGV failed" "$out" && return 1
  tail -10 "$out" | grep -q "JOB DONE" || return 1
  return 0
}
```

---

## 4. Validation Set (3-5 Representative Structures)

### Selection Protocol

1. After categorization, for each populated category, select the case with the **median wall-clock time** (representative cost)
2. If any category has only 1 case, that case IS the representative
3. Add one "hard case" — the non-converged case with the most SCF steps attempted
4. Target: exactly 5 structures

### Validation Procedure

For each of the 5 selected structures:
1. Apply the Category-specific parameter changes from Section 2
2. Submit with the same `TIME_LIMIT=12h` and node configuration
3. Check convergence using the 4-gate criterion from Section 3
4. Record: wall-clock time, final energy, final forces, SCF steps, convergence outcome

### Go/No-Go Decision

- **GO:** ≥4/5 converge → proceed with full 30-case re-run
- **CONDITIONAL:** 3/5 converge → analyze 2 failures, adjust, re-validate
- **NO-GO:** ≤2/5 converge → escalate to CTO; non-convergence may be structural/physical

---

## 5. Expected Wall-Clock per Re-run on xingyi

### Estimated Re-run Costs by Category

| Category | Parameter Changes | Expected SCF Steps | Expected Wall-Clock | Assessment |
|---|---|---|---|---|
| A (oscillation) | Lower AMIX/BMIX, fix MAGMOM | 80-150 | 4-6 h | PASS |
| B (stagnation) | NELM=300, ALGO=All | 150-250 | 8-10 h | MARGINAL |
| C (divergence) | Relaxed EDIFFG, ISIF=2 | 100-200 | 6-8 h | PASS |
| D (time-limit) | ALGO=Fast, KPAR=4 | 60-120 | 3-5 h | PASS |
| E (k-point crash) | 3×3×3 mesh | 60-100 | 8-11 h | CONTINGENCY |

### 12h Budget Assessment

- Categories A, C, D: **Comfortable** (≤8h). PASS.
- Category B: **Tight** (8-10h). MARGINAL — if ALGO=All doesn't deliver, may need `ALGO=VeryFast` for early steps then switch.
- Category E: **Risky** (8-11h). CONTINGENCY — if validation exceeds 10h, fall back to 2×2×2 + `ALGO=All` + `NELM=400`.

### Total Compute Budget

Average ~6h/job × 30 jobs = 180 node-hours ≈ **7.5 node-days** on 32-core nodes. With 10-way parallelism on xingyi: **~18 hours wall-clock** for the full batch.

---

## Implementation Recommendation (Deliverable 4)

**Recommendation: Single dedicated batch** (not merged into existing submit loop).

Rationale:
1. Each case needs **category-specific** INCAR overrides — the existing loop uses uniform parameters
2. The dedupe guard (NFM-3380) would need category-aware logic
3. A batch script with a `case_params.tsv` mapping is simpler to audit and roll back
4. Runs concurrently with remaining first-pass jobs without interference

---

## Handoff Bar Recommendation (Deliverable 3)

**Preliminary:** ≥350/441 (79.4%)

| Scenario | Recovered from 30 | Total Converged | Rate |
|---|---|---|---|
| Optimistic | 25 | 106 | 24.0% |
| Realistic | 18 | 99 | 22.4% |
| Pessimistic | 10 | 91 | 20.6% |

The remaining ~330 not-yet-run cases should converge at ~90% rate (matching the 81/90 first-batch rate), yielding ~297 more. A 350 bar gives confidence margin without requiring the 30-case rescue to be perfect.

**To be finalized after validation set results.**

---

*End of pre-registration plan. Validation set results will be reported as [RESEARCH-OUTPUT] updates on this issue.*
