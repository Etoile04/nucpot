#!/bin/bash
#SBATCH --job-name=forever-v7fix
#SBATCH --partition=deimos
#SBATCH --qos=normal
#SBATCH --ntasks-per-node=1
#SBATCH --time=7-00:00:00
#SBATCH --output=/dev/null

# NFM-1540 v7修复脚本 v4
# v3: heredoc去掉单引号使$basename/$work_dir正常展开
# v4 patch 2026-08-20 for NFM-3380: dedupe guard via shared helper.
#   - Replaced per-structure squeue call with cached single-query lookup.
#   - Cross-campaign dedup: skips if ANY campaign is running this structure.
#   - Fail-closed: if squeue is unavailable, skips the entire round.
#   - Removed old is_in_queue() function (replaced by queue_dedupe.sh).

source /etc/profile.d/modules.sh
module load intel/oneapi2024.2_impi
module load gcc/9.5.0
export PATH=/APP/u22/x86/bin:/APP/u22/x86/intel/oneapi2024.2/mpi/2021.13/bin:/APP/u22/x86/gcc/9.5.0/bin:$PATH
export TMPDIR=/HOME/npic_dsun/npic_dsun_6/HDD_POOL/tmp

LOG=/HOME/npic_dsun/npic_dsun_6/dft_pipeline/scaleup/forever_v7fix.log
PP=/HOME/npic_dsun/npic_dsun_6/uranium_pp/pbe/PSEUDOPOTENTIALS
BATCH=/HOME/npic_dsun/npic_dsun_6/dft_pipeline/scaleup/dft_54atom_top500
FIXDIR=$BATCH/runs_v7fix
DEDUPE_HELPER=/HOME/npic_dsun/npic_dsun_6/dft_pipeline/lib/queue_dedupe.sh

mkdir -p $FIXDIR
echo "FOREVER-V7FIX-V4 START $(date)" >> $LOG
echo "Fix: heredoc unquoted for variable expansion" >> $LOG
echo "Patch: NFM-3380 dedupe guard via $DEDUPE_HELPER" >> $LOG

source "$DEDUPE_HELPER"

while true; do
    REMAINING=0
    SUBMITTED_THIS_ROUND=0

    # Refresh squeue cache once per iteration (fail-closed: skips round on failure)
    if ! dedupe_refresh_cache; then
        echo "$(date +%H:%M:%S): ERROR: squeue unavailable, skipping round (fail-closed)" >> $LOG
        sleep 300
        continue
    fi

    for in_file in $BATCH/*.in; do
        basename=$(basename "$in_file" .in)
        orig_out="$BATCH/runs/comp_$basename/$basename.out"

        if [ -f "$orig_out" ] && grep -q "^!.*total energy" "$orig_out" 2>/dev/null; then
            continue
        fi

        fix_out="$FIXDIR/comp_$basename/$basename.out"
        if [ -f "$fix_out" ] && grep -q "^!.*total energy" "$fix_out" 2>/dev/null; then
            continue
        fi

        work_dir="$FIXDIR/comp_$basename"
        if [ -f "$work_dir/SKIP" ]; then
            continue
        fi

        if is_already_queued "comp_${basename}"; then
            REMAINING=$((REMAINING + 1))
            continue
        fi

        REMAINING=$((REMAINING + 1))

        # Use cached queue count instead of a separate squeue call
        Q=$(echo "$_DEDUPE_QUEUE_CACHE" | grep -c .)
        [ "$Q" -ge 28 ] && break

        work_dir="$FIXDIR/comp_$basename"
        mkdir -p "$work_dir"

        # 用awk清理参数
        awk '
        /^[[:space:]]*ecutwfc[[:space:]]*=/ { print "    ecutwfc = 60.0"; next }
        /^[[:space:]]*ecutrho[[:space:]]*=/ { print "    ecutrho = 480.0"; next }
        /^[[:space:]]*conv_thr[[:space:]]*=/ { print "    conv_thr = 1.0E-4"; next }
        /^[[:space:]]*mixing_beta[[:space:]]*=/ { print "    mixing_beta = 0.4"; next }
        /^[[:space:]]*mixing_mode[[:space:]]*=/ { print "    mixing_mode = \"local-TF\""; next }
        /^[[:space:]]*mixing_ndim[[:space:]]*=/ { print "    mixing_ndim = 10"; next }
        { print }
        ' "$in_file" > "$work_dir/$basename.in"

        # 添加mixing_mode和mixing_ndim
        if ! grep -q "mixing_mode" "$work_dir/$basename.in"; then
            sed -i '/mixing_beta/a\    mixing_mode = '"'"'local-TF'"'"'\n    mixing_ndim = 10' "$work_dir/$basename.in"
        fi

        # Symlink PPs
        for pp in $PP/*.UPF; do
            [ -f "$work_dir/$(basename $pp)" ] || ln -sf "$pp" "$work_dir/"
        done

        # 生成run.sh (heredoc不带引号, 变量会展开)
        cat > "$work_dir/run.sh" << RUNEOF
#!/bin/bash
source /etc/profile.d/modules.sh
module load intel/oneapi2024.2_impi
module load gcc/9.5.0
export PATH=/APP/u22/x86/bin:/APP/u22/x86/intel/oneapi2024.2/mpi/2021.13/bin:/APP/u22/x86/gcc/9.5.0/bin:\$PATH
export OMP_NUM_THREADS=1
export LD_LIBRARY_PATH=/APP/u22/x86/intel/oneapi2024.2/mkl/2024.2/lib:/APP/u22/x86/intel/oneapi2024.2/compiler/2024.2/lib:/APP/u22/x86/intel/oneapi2024.2/mpi/2021.13/lib:/APP/u22/x86/intel/oneapi2024.2/2024.2/lib:/APP/u22/x86/intel/oneapi2024.2/tbb/2021.13/lib:\$LD_LIBRARY_PATH
export TMPDIR=/HOME/npic_dsun/npic_dsun_6/HDD_POOL/tmp
cd $work_dir
/APP/u22/x86/intel/oneapi2024.2/mpi/2021.13/bin/mpirun -np 32 /HOME/npic_dsun/npic_dsun_6/HDD_POOL/qe-build/bin/pw.x -inp $basename.in > $basename.out 2>&1
RUNEOF
        chmod +x "$work_dir/run.sh"

        sbatch -p deimos -q normal -N 1 --ntasks-per-node=32 --time=02:00:00 \
               --job-name=v7fix-$basename --chdir=$work_dir \
               --output=$work_dir/slurm.out $work_dir/run.sh >/dev/null 2>&1
        SUBMITTED_THIS_ROUND=$((SUBMITTED_THIS_ROUND + 1))
    done

    echo "$(date +%H:%M:%S): remaining=$REMAINING, submitted=$SUBMITTED_THIS_ROUND" >> $LOG

    if [ "$REMAINING" -eq 0 ]; then
        echo "ALL DONE $(date)" >> $LOG
        CONV=0
        for d in $FIXDIR/comp_*/; do
            OUT=$(ls "$d"*.out 2>/dev/null | head -1)
            [ -n "$OUT" ] && grep -q "^!.*total energy" "$OUT" 2>/dev/null && CONV=$((CONV+1))
        done
        echo "v7fix converged: $CONV" >> $LOG
        break
    fi

    sleep 300
done
