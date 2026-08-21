#!/bin/bash
# batch-rerun.sh - QE-adapted batch re-run for the 30 non-converged 54-atom cases.
# Adapted from the VASP-targeted script committed at 9b8d9b486 (now .vasp).
# Reference: NFM-3381 analysis-plan.md Section 2 + Section 5
#
# Usage on xingyi:
#   # Step 1: Validate (≤5 cases, 5-way parallel, ~12h wall)
#   ./batch-rerun.sh --mode validation \
#       --case-params case-params.tsv \
#       --validation-set validation-set.tsv \
#       --workdir /XYFS02/npic_dsun_6/54atom_rerun_validation \
#       --base-template /HOME/.../runs/comp_comp_0001_III
#
#   # Step 2: After GO decision (≥4/5 in validation), full batch (30 cases, 10-way parallel)
#   ./batch-rerun.sh --mode full \
#       --case-params case-params.tsv \
#       --workdir /XYFS02/npic_dsun_6/54atom_rerun_full \
#       --base-template /HOME/.../runs/comp_comp_0001_III
#
# Inputs:
#   --case-params       TSV from categorize_cases.py (case_id, category, QE `key=value` overrides)
#   --validation-set    TSV from select_validation_set.py (representative cases)
#   --mode              validation | full
#   --workdir           Directory for per-case run outputs
#   --parallel          Max concurrent jobs (default 5 for validation, 10 for full)
#   --time-limit        SLURM time limit per job (default 12:00:00)
#   --base-template     Reference structure dir (.in + .upf files) — only the case_id differs
#   --nprocs            MPI ranks per job (default 4; xingyi run.sh template uses 4)

set -euo pipefail

MODE="validation"
CASE_PARAMS="case-params.tsv"
VALIDATION_SET="validation-set.tsv"
WORKDIR="/XYFS02/npic_dsun_6/54atom_rerun"
PARALLEL=""
TIME_LIMIT="12:00:00"
BASE_TEMPLATE=""
NPROCS="4"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --case-params) CASE_PARAMS="$2"; shift 2 ;;
    --validation-set) VALIDATION_SET="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    --parallel) PARALLEL="$2"; shift 2 ;;
    --time-limit) TIME_LIMIT="$2"; shift 2 ;;
    --base-template) BASE_TEMPLATE="$2"; shift 2 ;;
    --nprocs) NPROCS="$2"; shift 2 ;;
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
[[ -d "$BASE_TEMPLATE" ]] || { echo "ERROR: --base-template $BASE_TEMPLATE not found"; exit 1; }

# QE environment (from run.sh template: /HOME/.../runs/<case>/run.sh)
MPIRUN_BIN="/APP/u22/x86/intel/oneapi2024.2/mpi/2021.13/bin/mpirun"
PW_X_BIN="/HOME/npic_dsun/npic_dsun_6/HDD_POOL/qe-build/bin/pw.x"
[[ -x "$MPIRUN_BIN" ]] || { echo "ERROR: mpirun not found at $MPIRUN_BIN"; exit 1; }
[[ -x "$PW_X_BIN" ]] || { echo "ERROR: pw.x not found at $PW_X_BIN"; exit 1; }

mkdir -p "$WORKDIR"
echo "[===] Mode=$MODE input=$INPUT parallel=$PARALLEL workdir=$WORKDIR"
echo "[===] Start: $(date -u +%FT%TZ)"

# Build the per-case .in file by copying the base-template .in and rewriting the
# override lines (mixing_beta / electron_maxstep / conv_thr / diagonalization /
# starting_magnetization). The base-template's case_id (the QE `prefix = ...`
# line) and CELL_PARAMETERS / ATOMIC_POSITIONS are reused as-is.
build_incar() {
  local case_id="$1"
  local outdir="$2"

  local base_in
  base_in=$(ls "$BASE_TEMPLATE"/*.in 2>/dev/null | head -1)
  [[ -f "$base_in" ]] || { echo "ERROR: no .in file in $BASE_TEMPLATE"; return 1; }

  local case_prefix
  case_prefix=$(echo "$case_id" | sed 's/^comp_comp_/comp_/')
  sed "s/^[[:space:]]*prefix[[:space:]]*=[[:space:]]*'[^']*'/    prefix = '$case_prefix'/" "$base_in" > "$outdir/$case_prefix.in"

  python3 - "$CASE_PARAMS" "$case_id" "$outdir/$case_prefix.in" <<'PYEOF'
import csv, sys, re
tsv_path, case_id, in_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(tsv_path) as f:
    rows = list(csv.DictReader(f, delimiter="\t"))
row = next((r for r in rows if r["case_id"] == case_id), None)
if row is None:
    sys.exit(f"No overrides row for {case_id}")
qe_overrides = {}
for col, val in row.items():
    if not val or col in ("case_id", "category", "wall_seconds", "scf_steps_attempted",
                          "final_scf_accuracy_Ry", "max_negative_rho", "notes"):
        continue
    if "=" in val:
        k, v = val.split("=", 1)
        qe_overrides[k.strip()] = v.strip()
with open(in_path) as f:
    lines = f.readlines()
keys = {re.match(r"\s*(\w+)\s*=", ln).group(1): i for i, ln in enumerate(lines) if re.match(r"\s*(\w+)\s*=", ln)}
new_lines = list(lines)
for k, v in qe_overrides.items():
    line = f"    {k} = {v}\n"
    if k in keys:
        new_lines[keys[k]] = line
    else:
        for i, ln in enumerate(new_lines):
            if ln.strip() == "/":
                new_lines.insert(i, line); break
        else:
            new_lines.append(line)
with open(in_path, "w") as f:
    f.writelines(new_lines)
PYEOF
}

submit_case() {
  local case_id="$1"
  local outdir="$WORKDIR/$case_id"
  mkdir -p "$outdir"

  # Copy base pseudopotentials (.upf)
  cp "$BASE_TEMPLATE"/*.upf "$outdir/" 2>/dev/null || true

  build_incar "$case_id" "$outdir" || return 1

  local prefix
  prefix=$(echo "$case_id" | sed 's/^comp_comp_/comp_/')
  local in_file="$outdir/$prefix.in"

  cd "$outdir"
  sbatch --job-name="54a5-rerun-$case_id" \
         --time="$TIME_LIMIT" \
         --ntasks="$NPROCS" \
         --cpus-per-task=1 \
         --output="$outdir/pw.out" \
         --wrap="export OMP_NUM_THREADS=4; $MPIRUN_BIN -np $NPROCS $PW_X_BIN -inp $in_file > pw.out 2>&1"
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
echo -e "case_id\tcategory\tconverged\twall_hours\tfinal_energy_Ry\tmax_force\tscf_steps\tgate_reasons" > "$RESULTS"
while IFS=$'\t' read -r case_id category _rest; do
  [[ "$case_id" == "case_id" ]] && continue
  [[ -z "$case_id" ]] && continue
  local prefix
  prefix=$(echo "$case_id" | sed 's/^comp_comp_/comp_/')
  OUTFILE="$WORKDIR/$case_id/pw.out"
  if [[ ! -f "$OUTFILE" ]]; then
    echo -e "$case_id\t$category\tNO\t\t\t\t\tno_output_file" >> "$RESULTS"
    continue
  fi
  if convergence-gate.sh "$OUTFILE" >/dev/null 2>gate_reason.txt; then
    CONV="YES"; REASON=""
  else
    CONV="NO"; REASON=$(cat gate_reason.txt)
  fi
  WALL=$(grep -E "PWSCF" "$OUTFILE" 2>/dev/null | tail -1 | awk '{print $NF}')
  WALL_HOURS=""
  if [[ -n "$WALL" ]]; then
    H=$(echo "$WALL" | awk -F'h' '{print $1}')
    M=$(echo "$WALL" | awk -F'h' '{print $2}' | tr -d 'm')
    WALL_HOURS=$(awk -v h="$H" -v m="$M" 'BEGIN{printf "%.2f", h + m/60.0}')
  fi
  ENERGY=$(grep "!    total energy" "$OUTFILE" 2>/dev/null | tail -1 | awk -F'=' '{print $2}' | awk '{print $1}')
  FORCE=$(grep "Total force" "$OUTFILE" 2>/dev/null | tail -1 | awk '{print $3}')
  STEPS=$(grep -c "estimated scf accuracy" "$OUTFILE" 2>/dev/null || echo 0)
  echo -e "$case_id\t$category\t$CONV\t$WALL_HOURS\t$ENERGY\t$FORCE\t$STEPS\t$REASON" >> "$RESULTS"
done < <(awk -F'\t' 'NR==1 || $1!="" {print $1"\t"$2}' "$INPUT")

echo "[===] Results: $RESULTS"
echo "[===] Converged: $(tail -n +2 "$RESULTS" | awk -F'\t' '$3=="YES"' | wc -l) / $(tail -n +2 "$RESULTS" | wc -l)"

if [[ "$MODE" == "validation" ]]; then
  CONV_COUNT=$(tail -n +2 "$RESULTS" | awk -F'\t' '$3=="YES"' | wc -l)
  TOTAL=$(tail -n +2 "$RESULTS" | wc -l)
  echo "[===] Validation outcome: $CONV_COUNT / $TOTAL"
  if [[ "$TOTAL" -eq 0 ]]; then
    echo "[===] VERDICT: NO-GO - no cases submitted"
  elif [[ $CONV_COUNT -eq $TOTAL ]]; then
    echo "[===] VERDICT: GO - proceed with full 30-case batch"
  elif [[ $CONV_COUNT -ge $((TOTAL * 2 / 3)) ]]; then
    echo "[===] VERDICT: CONDITIONAL - analyze failures before full batch"
  else
    echo "[===] VERDICT: NO-GO - escalate to NDE; non-convergence may be structural"
  fi
fi