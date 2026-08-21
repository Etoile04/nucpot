#!/bin/bash
# convergence-gate.sh - 4-gate convergence criterion for Quantum ESPRESSO runs.
# Adapted from the VASP-targeted script committed at 9b8d9b486 (now .vasp).
#
# A QE re-run is CONVERGED iff ALL of:
#   1. Electronic convergence: final "estimated scf accuracy" < conv_thr
#      (read from sibling .in file's `conv_thr = X` value)
#   2. Ionic convergence (if calculation = 'relax'/'vc-relax'): final
#      "Total force" below 1.0e-3 Ry/au
#   3. No divergence markers: no "convergence NOT achieved",
#      no "Sub-Space-Matrix is not hermitian", no "ZHEGV failed"
#   4. "JOB DONE." present in the final 10 lines
#
# Usage:
#   convergence-gate.sh <path-to-pw.out>
#   echo $?  # 0 = converged, 1 = not converged
#
# Returns 0 (converged) or 1 (not converged); logs reasons on stderr.

set -euo pipefail

out="${1:?usage: convergence-gate.sh <pw.out>}"

if [[ ! -f "$out" ]]; then
  echo "ERROR: $out not found" >&2
  exit 2
fi

reasons=()

# Gate 3 (do this first - fast rejection of obviously broken runs)
if grep -qE "convergence NOT achieved|Sub-Space-Matrix is not hermitian|EDDDAV: Call to ZHEGV failed" "$out"; then
  reasons+=("divergence_markers_present")
fi

# Gate 4 - JOB DONE in last 10 lines
if ! tail -10 "$out" | grep -q "JOB DONE"; then
  reasons+=("no_job_done_in_tail")
fi

# Gate 1 - electronic convergence: final estimated scf accuracy < conv_thr
final_acc=$(grep "estimated scf accuracy" "$out" 2>/dev/null | tail -1 | awk -F'<' '{print $2}' | awk '{print $1}')
if [[ -z "$final_acc" ]]; then
  reasons+=("electronic_no_scf_accuracy_data")
else
  in_file="${out%.out}.in"
  conv_thr=""
  if [[ -f "$in_file" ]]; then
    conv_thr=$(awk '/conv_thr/ {print $3; exit}' "$in_file" 2>/dev/null || echo "")
  fi
  if [[ -z "$conv_thr" ]]; then
    conv_thr="1.0d-3"
  fi
  # Compare using python (translate FORTRAN "1.0d-3" → Python "1.0e-3")
  acc_e=$(echo "$final_acc" | tr 'dD' 'eE')
  thr_e=$(echo "$conv_thr" | tr 'dD' 'eE')
  if ! python3 -c "import sys; sys.exit(0 if float('$acc_e') < float('$thr_e') else 1)" 2>/dev/null; then
    reasons+=("electronic_not_converged_acc=${final_acc}_conv_thr=${conv_thr}")
  fi
fi

# Gate 2 - ionic convergence (if calculation is relax/vc-relax)
in_file="${out%.out}.in"
calc="scf"
if [[ -f "$in_file" ]]; then
  calc=$(awk -F"'" '/calculation/ {print $2; exit}' "$in_file" 2>/dev/null || echo "scf")
fi
if [[ "$calc" == "relax" || "$calc" == "vc-relax" ]]; then
  max_force=$(grep "Total force" "$out" 2>/dev/null | tail -1 | awk '{print $3}')
  if [[ -z "$max_force" ]]; then
    reasons+=("ionic_no_force_data")
  force_e=$(echo "$max_force" | tr 'dD' 'eE')
  elif ! python3 -c "import sys; sys.exit(0 if float('$force_e') < 1.0e-3 else 1)" 2>/dev/null; then
    reasons+=("ionic_not_converged_force=${max_force}")
  fi
fi

if [[ ${#reasons[@]} -eq 0 ]]; then
  echo "CONVERGED" >&2
  exit 0
else
  echo "NOT_CONVERGED: ${reasons[*]}" >&2
  exit 1
fi