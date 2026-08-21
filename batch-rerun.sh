#!/bin/bash
# batch-rerun.sh - Dedicated batch re-run template for the 30 non-converged 54-atom cases
# Reference: NFM-3381 analysis-plan.md Section 2 + Section 5
#
# Usage on xingyi:
#   # Step 1: Validate (5 cases, 5-way parallel, ~6h wall)
#   ./batch-rerun.sh --mode validation \
#       --case-params case-params.tsv \
#       --validation-set validation-set.tsv \
#       --workdir /scratch/54atom_rerun_validation \
#       --base-template /HOME/.../runs/comp_0001_III
#
#   # Step 2: After GO decision (>=4/5), full batch (30 cases, 10-way parallel, ~18h wall)
#   ./batch-rerun.sh --mode full \
#       --case-params case-params.tsv \
#       --workdir /scratch/54atom_rerun_full \
#       --base-template /HOME/.../runs/comp_0001_III
#
# Inputs:
#   --case-params       TSV from categorize_cases.py (case_id, category, INCAR overrides)
#   --validation-set    TSV from select_validation_set.py (5 representative cases)
#   --mode              validation | full
#   --workdir           Directory for per-case run outputs
#   --parallel          Max concurrent jobs (default 5 for validation, 10 for full)
#   --time-limit        SLURM time limit per job (default 12:00:00)
#   --base-template     Reference structure dir for non-overridden files (POSCAR, POTCAR)

set -euo pipefail

MODE="validation"
CASE_PARAMS="case-params.tsv"
VALIDATION_SET="validation-set.tsv"
WORKDIR="/scratch/54atom_rerun"
PARALLEL=""
TIME_LIMIT="12:00:00"
BASE_TEMPLATE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --case-params) CASE_PARAMS="$2"; shift 2 ;;
    --validation-set) VALIDATION_SET="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    --parallel) PARALLEL="$2"; shift 2 ;;
    --time-limit) TIME_LIMIT="$2"; shift 2 ;;
    --base-template) BASE_TEMPLATE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$PARALLEL" ]]; then
  if [[ "$MODE" == "validation" ]]; then
    PARALLEL=5
  else
    PARALLEL=10
  fi
fi

if [[ "$MODE" == "validation" ]]; then
  INPUT="$VALIDATION_SET"
else
  INPUT="$CASE_PARAMS"
fi

[[ -f "$INPUT" ]] || { echo "ERROR: $INPUT not found"; exit 1; }
[[ -f "convergence-gate.sh" ]] || { echo "ERROR: convergence-gate.sh missing (run from this directory)"; exit 1; }
[[ -n "$BASE_TEMPLATE" ]] || { echo "ERROR: --base-template required (reference structure dir)"; exit 1; }

mkdir -p "$WORKDIR"
echo "[===] Mode=$MODE input=$INPUT parallel=$PARALLEL workdir=$WORKDIR"
echo "[===] Start: $(date -u +%FT%TZ)"

submit_case() {
  local case_id="$1"
  local outdir="$WORKDIR/$case_id"
  mkdir -p "$outdir"

  cp "$BASE_TEMPLATE"/POSCAR "$outdir/" 2>/dev/null || true
  cp "$BASE_TEMPLATE"/POTCAR "$outdir/" 2>/dev/null || true

  python3 - "$CASE_PARAMS" "$case_id" > "$outdir/INCAR" <<'PYEOF'
import csv, sys
tsv_path, case_id = sys.argv[1], sys.argv[2]
with open(tsv_path) as f:
    for row in csv.DictReader(f, delimiter="\t"):
        if row["case_id"] == case_id:
            for k, v in row.items():
                if v and k not in ("case_id", "category", "wall_seconds",
                                    "scf_steps_attempted", "notes"):
                    print(f"{k} = {v}")
            sys.exit(0)
print(f"# No overrides for {case_id}", file=sys.stderr)
PYEOF

  cd "$outdir"
  sbatch --job-name="54a5-rerun-$case_id" \
         --time="$TIME_LIMIT" \
         --output="$outdir/vasp.out" \
         --wrap="mpirun -np 32 vasp_std > vasp.out 2>&1"
  cd - >/dev/null
}

i=0
while IFS=$'\t' read -r case_id _rest; do
  [[ "$case_id" == "case_id" ]] && continue
  [[ -z "$case_id" ]] && continue

  while [[ $(squeue -u "$USER" -h 2>/dev/null | wc -l) -ge $PARALLEL ]]; do
    sleep 60
  done

  echo "[+] Submitting $case_id at $(date -u +%FT%TZ)"
  submit_case "$case_id"
  i=$((i+1))
done < <(awk -F'\t' 'NR==1 || $1!="" {print $1"\t"$2}' "$INPUT")

echo "[===] Submitted $i cases"

while [[ $(squeue -u "$USER" -h 2>/dev/null | wc -l) -gt 0 ]]; do
  sleep 300
done
echo "[===] All jobs complete at $(date -u +%FT%TZ)"

RESULTS="$WORKDIR/results.tsv"
echo -e "case_id\tcategory\tconverged\twall_hours\tfinal_energy_eV\tmax_force\tscf_steps\tgate_reasons" > "$RESULTS"
while IFS=$'\t' read -r case_id category _rest; do
  [[ "$case_id" == "case_id" ]] && continue
  [[ -z "$case_id" ]] && continue
  OUTFILE="$WORKDIR/$case_id/vasp.out"
  if [[ ! -f "$OUTFILE" ]]; then
    echo -e "$case_id\t$category\tNO\t\t\t\t\tno_output_file" >> "$RESULTS"
    continue
  fi
  if convergence-gate.sh "$OUTFILE" >/dev/null 2>gate_reason.txt; then
    CONV="YES"; REASON=""
  else
    CONV="NO"; REASON=$(cat gate_reason.txt)
  fi
  WALL=$(grep "Elapsed time" "$OUTFILE" 2>/dev/null | tail -1 | awk '{print $4}')
  ENERGY=$(grep "energy without entropy" "$OUTFILE" 2>/dev/null | tail -1 | awk '{print $7}')
  FORCE=$(grep "FORCES: max" "$OUTFILE" 2>/dev/null | tail -1 | awk '{print $4}')
  STEPS=$(grep -c "DAV:\|RMM:" "$OUTFILE" 2>/dev/null || echo 0)
  echo -e "$case_id\t$category\t$CONV\t$WALL\t$ENERGY\t$FORCE\t$STEPS\t$REASON" >> "$RESULTS"
done < <(awk -F'\t' 'NR==1 || $1!="" {print $1"\t"$2}' "$INPUT")

echo "[===] Results: $RESULTS"
echo "[===] Converged: $(tail -n +2 "$RESULTS" | awk -F'\t' '$3=="YES"' | wc -l) / $(tail -n +2 "$RESULTS" | wc -l)"

if [[ "$MODE" == "validation" ]]; then
  CONV_COUNT=$(tail -n +2 "$RESULTS" | awk -F'\t' '$3=="YES"' | wc -l)
  echo "[===] Validation outcome: $CONV_COUNT / 5"
  if [[ $CONV_COUNT -ge 4 ]]; then
    echo "[===] VERDICT: GO - proceed with full 30-case batch"
  elif [[ $CONV_COUNT -ge 3 ]]; then
    echo "[===] VERDICT: CONDITIONAL - analyze 2 failures before full batch"
  else
    echo "[===] VERDICT: NO-GO - escalate to CTO; non-convergence may be structural"
  fi
fi