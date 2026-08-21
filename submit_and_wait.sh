#!/bin/bash
set -euo pipefail

WORKDIR="/XYFS02/npic_dsun_6/54atom_rerun_validation"
CASES=("comp_comp_0049_I" "comp_comp_0275_II" "comp_comp_0045_I" "comp_comp_0284_I" "comp_comp_0029_II")
MPIRUN_BIN="/APP/u22/x86/intel/oneapi2024.2/mpi/2021.13/bin/mpirun"
PW_X_BIN="/HOME/npic_dsun/npic_dsun_6/HDD_POOL/qe-build/bin/pw.x"
MODULE_SETUP="source /etc/profile.d/modules.sh; module load intel/oneapi2024.2_impi 2>/dev/null; module load gcc/9.5.0 2>/dev/null; export PATH=/APP/u22/x86/bin:/APP/u22/x86/intel/oneapi2024.2/mpi/2021.13/bin:/APP/u22/x86/gcc/9.5.0/bin:\$PATH; export OMP_NUM_THREADS=4; export LD_LIBRARY_PATH=/APP/u22/x86/intel/oneapi2024.2/mkl/2024.2/lib:/APP/u22/x86/intel/oneapi2024.2/compiler/2024.2/lib:/APP/u22/x86/intel/oneapi2024.2/mpi/2021.13/lib:/APP/u22/x86/intel/oneapi2024.2/2024.2/lib:/APP/u22/x86/intel/oneapi2024.2/tbb/2021.13/lib:\$LD_LIBRARY_PATH; export TMPDIR=/HOME/npic_dsun/npic_dsun_6/HDD_POOL/tmp"
LOG="/HOME/npic_dsun/npic_dsun_6/NFM-3381-worktree/submit_and_wait.log"

exec > >(tee -a "$LOG") 2>&1

echo "[START] $(date -u +%FT%TZ)"
echo "[INFO] MaxSubmitJobs=32, current=$(squeue -u npic_dsun_6 -h | wc -l)"

# Submit phase: wait for slots and submit one by one
JOB_IDS=()
for case_dir in "${CASES[@]}"; do
  in_file=$(ls $WORKDIR/$case_dir/*.in 2>/dev/null | head -1)
  if [[ -z "$in_file" ]]; then
    echo "[ERROR] No .in for $case_dir"
    continue
  fi

  # Wait for slot (< 32 total)
  while true; do
    cur=$(squeue -u npic_dsun_6 -h 2>/dev/null | wc -l)
    if [[ $cur -lt 32 ]]; then break; fi
    echo "[WAIT] $cur/32 jobs, sleeping 120s... $(date -u +%FT%TZ)"
    sleep 120
  done

  jid=$(sbatch --partition=deimos --job-name="val-$case_dir" \
    --time=12:00:00 --ntasks=4 --cpus-per-task=1 \
    --output="$WORKDIR/$case_dir/pw.out" \
    --wrap="$MODULE_SETUP; cd $WORKDIR/$case_dir; $MPIRUN_BIN -np 4 $PW_X_BIN -inp $in_file > pw.out 2>&1" 2>&1 | awk '{print $4}')
  echo "[SUBMIT] $case_dir -> JOB $jid (queue was $cur/32)"
  JOB_IDS+=("$jid")
done

echo "[SUBMIT-DONE] All submitted: ${JOB_IDS[*]}"

# Wait for all jobs to finish
for jid in "${JOB_IDS[@]}"; do
  while squeue -j "$jid" -h 2>/dev/null | grep -q .; do
    sleep 300
  done
  state=$(sacct -j "$jid" -n -X -o State 2>/dev/null | tr -d ' ')
  echo "[DONE] JOB $jid -> $state"
done

echo "[ALL-DONE] $(date -u +%FT%TZ)"
