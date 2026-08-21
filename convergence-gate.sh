#!/bin/bash
# convergence-gate.sh - Implement the 4-gate convergence criterion from
# NFM-3381 analysis-plan.md Section 3.
#
# A re-run is CONVERGED iff ALL of:
#   1. Electronic convergence: .out contains "reached required accuracy"
#      OR final SCF step shows energy change < EDIFF (<= 1E-6)
#   2. Ionic convergence (if ISIF>=2): final "FORCES: max" < |EDIFFG| threshold
#   3. No divergence markers: no "convergence NOT achieved",
#      no "Sub-Space-Matrix is not hermitian", no "ZHEGV failed"
#   4. "JOB DONE" present in the final 10 lines (secondary confirmation)
#
# Usage:
#   convergence-gate.sh <path-to-vasp.out>
#   echo $?  # 0 = converged, 1 = not converged
#
# Returns 0 (converged) or 1 (not converged); logs reasons on stderr.

set -euo pipefail

out="${1:?usage: convergence-gate.sh <vasp.out>}"

if [[ ! -f "$out" ]]; then
  echo "ERROR: $out not found" >&2
  exit 2
fi

reasons=()

# Gate 3 (do this first - fast rejection of obviously broken runs)
if grep -qE "convergence NOT achieved|Sub-Space-Matrix is not hermitian|EDDDAV: Call to ZHEGV failed" "$out"; then
  reasons+=("divergence_markers_present")
fi

# Gate 1 - electronic convergence
if grep -q "reached required accuracy" "$out"; then
  : # electronic OK
else
  # Check last SCF step energy change
  final_de=$(awk '
    /energy without entropy/ { prev=curr; curr=$NF; if (NR>1 && prev!="") { d=curr-prev; if (d<0) d=-d; if (d<last_d || last_d=="") last_d=d } }
    END { print last_d+0 }
  ' "$out" 2>/dev/null || echo "")
  ediff=$(awk '/EDIFF/ {print $3; exit}' "$out" 2>/dev/null || echo "1E-6")
  if [[ -z "$final_de" ]] || ! python3 -c "import sys; sys.exit(0 if float('$final_de') < float('$ediff') else 1)" 2>/dev/null; then
    reasons+=("electronic_not_converged_dE=${final_de}_ediff=${ediff}")
  fi
fi

# Gate 2 - ionic convergence (if ISIF >= 2)
isif=$(awk '/ISIF/ {print $3; exit}' "$out" 2>/dev/null || echo "2")
if [[ "${isif:-2}" -ge 2 ]]; then
  max_force=$(awk '/FORCES: max/ {print $4; exit}' "$out" 2>/dev/null || echo "")
  ediffg=$(awk '/EDIFFG/ {print $3; exit}' "$out" 2>/dev/null || echo "-0.01")
  if [[ -z "$max_force" ]]; then
    reasons+=("ionic_no_force_data")
  elif ! python3 -c "import sys; sys.exit(0 if float('$max_force') < abs(float('$ediffg')) else 1)" 2>/dev/null; then
    reasons+=("ionic_not_converged_force=${max_force}_ediffg=${ediffg}")
  fi
fi

# Gate 4 - JOB DONE in last 10 lines
if ! tail -10 "$out" | grep -q "JOB DONE"; then
  reasons+=("no_job_done_in_tail")
fi

if [[ ${#reasons[@]} -eq 0 ]]; then
  echo "CONVERGED" >&2
  exit 0
else
  echo "NOT_CONVERGED: ${reasons[*]}" >&2
  exit 1
fi