#!/bin/bash
# Evaluate validation re-run results against 4-gate convergence criterion.
# Gate 1: electronic convergence (convergence achieved)
# Gate 2: ionic convergence (forces < threshold)
# Gate 3: no divergence (total energy not exploding)
# Gate 4: JOB DONE (clean exit)
# Reads pw.out files from /XYFS02/npic_dsun_6/54atom_rerun_validation/
# Outputs results.tsv and verdict to /HOME/npic_dsun/npic_dsun_6/NFM-3381-worktree/

set -euo pipefail

WORKDIR="/XYFS02/npic_dsun_6/54atom_rerun_validation"
OUTDIR="/HOME/npic_dsun/npic_dsun_6/NFM-3381-worktree"
GATE_SCRIPT="$OUTDIR/convergence-gate.sh"
VALIDATION_SET="$OUTDIR/validation-set.tsv"
TAB=$'\t'

echo "[EVAL-START] $(date -u +%FT%TZ)"

RESULTS="$OUTDIR/results.tsv"
echo "case_id${TAB}category${TAB}converged${TAB}wall_hours${TAB}energy_Ry${TAB}force_eV_A${TAB}scf_steps${TAB}reason" > "$RESULTS"

while IFS= read -r line; do
  case_id=$(echo "$line" | cut -f1)
  category=$(echo "$line" | cut -f2)
  [[ "$case_id" == "case_id" ]] && continue
  [[ -z "$case_id" ]] && continue

  OUTFILE="$WORKDIR/$case_id/pw.out"
  if [[ ! -f "$OUTFILE" ]]; then
    echo "${case_id}${TAB}${category}${TAB}NO${TAB}${TAB}${TAB}${TAB}${TAB}no_output_file" >> "$RESULTS"
    echo "[EVAL] $case_id: NO OUTPUT FILE"
    continue
  fi

  # Check file size (empty = crash)
  fsize=$(stat -c%s "$OUTFILE" 2>/dev/null || echo 0)
  if [[ $fsize -lt 100 ]]; then
    echo "${case_id}${TAB}${category}${TAB}NO${TAB}${TAB}${TAB}${TAB}${TAB}empty_output ($fsize bytes)" >> "$RESULTS"
    echo "[EVAL] $case_id: EMPTY OUTPUT ($fsize bytes)"
    continue
  fi

  # Run convergence gate if available
  if [[ -x "$GATE_SCRIPT" ]]; then
    if bash "$GATE_SCRIPT" "$OUTFILE" >/dev/null 2>"$WORKDIR/gate_reason.txt"; then
      CONV="YES"
      REASON=""
    else
      CONV="NO"
      REASON=$(cat "$WORKDIR/gate_reason.txt" | head -1)
    fi
  else
    # Manual gate check
    if grep -q "JOB DONE" "$OUTFILE" && \
       grep -q "convergence has been achieved" "$OUTFILE"; then
      CONV="YES"
      REASON=""
    else
      CONV="NO"
      if ! grep -q "JOB DONE" "$OUTFILE"; then
        REASON="no_JOB_DONE"
      elif ! grep -q "convergence has been achieved" "$OUTFILE"; then
        REASON="scf_not_converged"
      else
        REASON="unknown"
      fi
    fi
  fi

  # Extract metrics
  WALL=$(grep -E "PWSCF" "$OUTFILE" 2>/dev/null | tail -1 | awk '{print $NF}')
  WALL_H=""
  if [[ -n "$WALL" ]]; then
    H=$(echo "$WALL" | awk -F'h' '{print $1}')
    M=$(echo "$WALL" | awk -F'h' '{print $2}' | tr -d 'm')
    WALL_H=$(awk -v h="$H" -v m="$M" 'BEGIN{printf "%.2f", h + m/60.0}')
  fi

  ENERGY=$(grep "!    total energy" "$OUTFILE" 2>/dev/null | tail -1 | awk -F'=' '{print $2}' | awk '{print $1}')
  FORCE=$(grep "Total force" "$OUTFILE" 2>/dev/null | tail -1 | awk '{print $NF}')
  STEPS=$(grep -c "estimated scf accuracy" "$OUTFILE" 2>/dev/null || echo 0)

  echo "${case_id}${TAB}${category}${TAB}${CONV}${TAB}${WALL_H}${TAB}${ENERGY}${TAB}${FORCE}${TAB}${STEPS}${TAB}${REASON}" >> "$RESULTS"
  echo "[EVAL] $case_id: $CONV (wall=${WALL_H}h, steps=$STEPS, energy=$ENERGY, force=$FORCE)"
done < "$VALIDATION_SET"

echo ""
echo "[RESULTS]"
cat "$RESULTS"

echo ""
CONV_COUNT=$(tail -n +2 "$RESULTS" | awk -F"$TAB" '$3=="YES"' | wc -l)
TOTAL=$(tail -n +2 "$RESULTS" | wc -l)
echo "[===] Validation: $CONV_COUNT / $TOTAL converged"

if [[ $TOTAL -eq 0 ]]; then
  echo "[===] VERDICT: NO-GO (no results)"
elif [[ $CONV_COUNT -eq $TOTAL ]]; then
  echo "[===] VERDICT: GO ($CONV_COUNT/$TOTAL)"
elif [[ $CONV_COUNT -ge $((TOTAL * 2 / 3 + 1)) ]]; then
  echo "[===] VERDICT: CONDITIONAL ($CONV_COUNT/$TOTAL)"
else
  echo "[===] VERDICT: NO-GO ($CONV_COUNT/$TOTAL)"
fi

echo "[EVAL-DONE] $(date -u +%FT%TZ)"
