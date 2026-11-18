"""Generate the linux/amd64 cross-platform Pareto fixture (NFM-5481 / NFM-6009 Q2).

Runs the SAME pinned NSGA-II configuration as ``generate_golden.py``
(pop 200, gen 100, seed 42) on linux/amd64 — CI's architecture —
normalizes the resulting front against the golden's per-axis spans, and
writes the normalized front plus the three guard metrics (computed with
the regression test module's own helpers) to the fixture JSON.

History: the original fixture (PR #1400 CI run 2, NFM-5063) froze the
rank-paired misread of an identical-region front. The NFM-5481
re-baseline (g5-g7 + U [0.80, 0.95]) made that artifact a PRE-floor
fixture by construction — it read ~3.3% Hausdorff against the post-floor
golden and stranded the guard test under ``xfail``. NFM-6009 Q2 blessed
regenerating it under the re-baselined harness with two conditions: the
rank-pairing lesson re-pinned synthetically in the regression module,
and schema_version 2 + a machine-readable problem-identity block on
golden AND fixture.

Run inside an linux/amd64 container (see NFM-6011 for the full recipe)::

    docker run --platform linux/amd64 --rm -v "$PWD":/repo -w /repo/apps/api \
      -e PYTHONPATH=/repo/apps/api/src \
      -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 -e MKL_NUM_THREADS=1 \
      -e NUMEXPR_NUM_THREADS=1 \
      nucpot-regen-base:amd64-nfm5481 \
      python3 tests/test_optimizer/generate_linux_fixture.py \
        --prediction-service-sha <40-hex sha from the host worktree>

``--prediction-service-sha`` exists for container runs: the worktree's
``.git`` file points at the host checkout, so in-container ``git``
cannot resolve provenance SHAs. Pass the host-worktree SHA explicitly;
it is validated to be a 40-char hex commit hash before use. On a host
with a working checkout the flag may be omitted and the SHA is read
from git directly.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import sys
from pathlib import Path

# Same thread pin as generate_golden.py / tests/conftest.py — the fixture
# must be generated under the same single-threaded BLAS regime the tests
# run under. Must precede the numpy import below.
for _threads_var in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_threads_var, "1")

import numpy as np  # noqa: E402 (thread pin above must precede numpy)
import pymoo  # noqa: E402

# Script-directory imports: the generator shares the pinned harness knobs
# and helpers with the golden generator and the regression module rather
# than re-defining them (a divergent copy would be a silent-mixup vector).
from generate_golden import (  # noqa: E402
    _PREDICTION_SERVICE,
    N_GEN,
    POP_SIZE,
    SEED,
    _git_sha_for,
    _run_nsga2,
    build_problem_identity,
)
from test_zr_pareto_regression import (  # noqa: E402
    HAUSDORFF_MAX,
    PER_AXIS_DRIFT_MAX,
    _canonical_order,
    _hausdorff_normalized,
    _normalize_axis,
    _per_axis_drift,
)

_USE_NEW_ALIAS = hasattr(_dt, "UTC")
_NOW = (
    (lambda: _dt.datetime.now(tz=_dt.UTC))  # type: ignore[attr-defined]
    if _USE_NEW_ALIAS
    else (lambda: _dt.datetime.now(tz=_dt.timezone.utc))  # noqa: UP017
)

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "test_zr_pareto_linux_cross_platform_fixture.json"
GOLDEN_PATH = HERE / "test_zr_pareto_golden.json"

#: The PRE-floor artifact this fixture supersedes (kept in-tree as an
#: archival artifact per the NFM-6009 Q2 recommendation).
ARCHIVAL_FIXTURE_NAME = (
    "test_zr_pareto_linux_cross_platform_fixture_prefloor_archival.json"
)

_AXIS_KEYS = ("neg_rho_U", "neg_T_stable", "neg_fabricability")
_SPAN_KEYS = ("rho_U", "T_stable", "fabricability")


def _platform_block() -> dict:
    """Machine-readable platform identity for the provenance block."""
    machine = platform.machine().lower()
    machine = {"amd64": "amd64", "x86_64": "amd64"}.get(machine, machine)
    return {
        "os": platform.system(),
        "machine": machine,
        "slug": f"{platform.system().lower()}/{machine}",
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "pymoo": pymoo.__version__,
    }


def _golden_minimized_front(golden: dict) -> np.ndarray:
    """Reconstruct the golden front array (minimization sense)."""
    return np.array(
        [
            [s["objectives_minimized"][key] for key in _AXIS_KEYS]
            for s in golden["front"]["solutions"]
        ],
        dtype=np.float64,
    )


def _span_arrays(golden: dict) -> tuple[np.ndarray, np.ndarray]:
    spans = golden["front"]["objective_axis_spans"]
    fmin = np.array([spans[k]["fmin"] for k in _SPAN_KEYS])
    fmax = np.array([spans[k]["fmax"] for k in _SPAN_KEYS])
    return fmin, fmax


def _resolve_prediction_service_sha(explicit: str) -> tuple[str, str]:
    """Return (sha, source) for the surrogate provenance pin."""
    sha = (explicit or "").strip()
    if sha:
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise SystemExit(
                f"--prediction-service-sha must be a 40-char hex commit "
                f"hash; got {sha!r}"
            )
        return sha, "cli-argument (host worktree git)"
    sha = _git_sha_for(_PREDICTION_SERVICE)
    if len(sha) == 40:
        return sha, "in-process git"
    raise SystemExit(
        "prediction_service.py commit SHA unresolved: no --prediction-"
        "service-sha given and git is unavailable in this environment "
        "(typical inside a container). Re-run with the SHA from the "
        "host worktree: git log -1 --pretty=format:%H -- "
        "apps/api/src/nfm_db/ml/prediction_service.py"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=FIXTURE_PATH,
        help="Fixture output path (default: alongside this script).",
    )
    parser.add_argument(
        "--golden", type=Path, default=GOLDEN_PATH,
        help="Golden JSON to normalize and compare against.",
    )
    parser.add_argument(
        "--prediction-service-sha", default="",
        help="40-hex commit SHA of prediction_service.py (required "
             "inside containers where git cannot resolve the host "
             "worktree).",
    )
    parser.add_argument(
        "--print-only", action="store_true",
        help="Print the front summary + guard metrics, do not write.",
    )
    args = parser.parse_args(argv)

    golden = json.loads(args.golden.read_text())
    fmin, fmax = _span_arrays(golden)

    print(
        f"[fixture] running NSGA-II: pop={POP_SIZE}, n_gen={N_GEN}, "
        f"seed={SEED} on {platform.system()}/{platform.machine()}"
    )
    F, _X, n_eval, wall_s = _run_nsga2()
    print(
        f"[fixture] front ready: {F.shape[0]} solutions, "
        f"{n_eval} evals, {wall_s:.2f}s wall"
    )

    cur_norm = _normalize_axis(F, fmin, fmax)
    gold_norm = _normalize_axis(_golden_minimized_front(golden), fmin, fmax)

    # The three guard metrics, computed with the regression module's own
    # helpers (NFM-6011 acceptance criterion 2) — not a parallel re-write.
    per_axis_set_drift = _per_axis_drift(cur_norm, gold_norm)
    hausdorff = _hausdorff_normalized(cur_norm, gold_norm)
    rank_paired = np.max(
        np.abs(_canonical_order(cur_norm) - _canonical_order(gold_norm)),
        axis=0,
    )

    sha, sha_source = _resolve_prediction_service_sha(
        args.prediction_service_sha
    )
    plat = _platform_block()

    payload = {
        "schema_version": 2,
        "issue": "NFM-5481",
        "purpose": (
            "Frozen cross-platform acceptance fixture: the normalized "
            "Zr-only Pareto front as computed on linux/amd64 (CI's "
            "architecture) under the re-baselined harness (g5-g7 + U "
            "[0.80, 0.95], NFM-5481). Regenerated under the NFM-6009 Q2 "
            "blessing; supersedes the PRE-floor PR #1400 CI run 2 "
            f"fixture (archived at {ARCHIVAL_FIXTURE_NAME})."
        ),
        "problem_identity": build_problem_identity(),
        "provenance": {
            "generated_at_utc": _NOW().isoformat(),
            "generator": "tests.test_optimizer.generate_linux_fixture",
            "platform": plat,
            "algorithm": {
                "framework": "pymoo>=0.6,<0.7",
                "algorithm": "NSGA-II",
                "pop_size": POP_SIZE,
                "n_gen": N_GEN,
                "seed": SEED,
                "n_evaluations": n_eval,
            },
            "surrogate_mode": (
                "synthetic (model artifacts absent in this environment; "
                "regression test runs identically so fronts stay "
                "comparable)"
            ),
            "provenance_files": {
                "prediction_service.py": sha,
                "prediction_service_sha_source": sha_source,
            },
            "n_solutions": int(F.shape[0]),
            "blessing": {
                "ruling_issue": "NFM-6009",
                "ruling_comment": "02c84af4-0e96-4890-9345-6ac493b9cfd0",
                "regen_issue": "NFM-6011",
            },
            "supersedes": {
                "fixture": ARCHIVAL_FIXTURE_NAME,
                "superseded_ci_sha": (
                    "e1fab9cb065da34e2c2abf676df3da1c200b02c7"
                ),
                "reason": (
                    "PRE role-spec-floor constraint set (U <= 0.90 "
                    "window, no g5-g7): different problem, not front "
                    "drift — read ~3.3% Hausdorff against the post-floor "
                    "golden."
                ),
            },
        },
        "metrics_vs_golden": {
            "rank_paired_elementwise": [float(v) for v in rank_paired],
            "per_axis_set_drift": [float(v) for v in per_axis_set_drift],
            "hausdorff_normalized": float(hausdorff),
        },
        "normalized_front": [
            [float(v) for v in row] for row in cur_norm
        ],
    }

    print(
        "[fixture] guard metrics vs golden (test-module helpers):\n"
        f"  per-axis set drift : {np.round(per_axis_set_drift, 6).tolist()}"
        f"  (tolerance max {PER_AXIS_DRIFT_MAX})\n"
        f"  Hausdorff (norm.)  : {hausdorff:.6f}"
        f"  (tolerance max {HAUSDORFF_MAX})\n"
        f"  rank-paired (canon): {np.round(rank_paired, 6).tolist()}"
    )

    if args.print_only:
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"[fixture] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
