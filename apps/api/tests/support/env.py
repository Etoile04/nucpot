"""Environment pins that must run before any numpy/xgboost-backed import.

NFM-5058 / PR #1400 run 2: pin BLAS/OpenMP thread pools to 1 BEFORE the
first numpy/xgboost-backed import below can load them. Threaded BLAS
reduction order is not stable across runs or hardware generations, and
with a fixed NSGA-II seed that 1e-16-level noise amplifies into a visibly
different Pareto front (CI per-axis drift 0.051 vs the 0.05 tolerance
against a golden that reproduces exactly on the generating box).

setdefault keeps an explicit caller override authoritative.
"""

from __future__ import annotations

import os

for _threads_var in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_threads_var, "1")
