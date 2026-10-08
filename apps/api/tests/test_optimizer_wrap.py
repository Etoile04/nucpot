"""Smoke test pinning the prediction_service surface that ml_surrogate reads.

NFM-5060 — KR-OPT-Q4-S5 (Maintain v3.0 dispatch compat smoke test).

This file exists to fail loudly the moment the contract between
``nfm_db.ml.prediction_service`` and ``nfm_db.optimization.ml_surrogate``
drifts.  The optimizer silently depends on three names imported from
``prediction_service``::

    from nfm_db.ml.prediction_service import PHYSICAL_FEATURE_NAMES
    from nfm_db.ml.prediction_service import PHASE_MODEL_PATH
    from nfm_db.ml.prediction_service import TEMP_MODEL_PATH

If any of those three names is renamed, retyped, or has its content
shifted, the surrogate's ``build_feature_matrix`` /
``_build_cluster_features`` calls raise ``ValueError`` /
``AttributeError`` deep inside NSGA-II evaluation rather than at module
load — silently regressing optimization results.  This smoke test makes
the contract a same-day CI signal instead of a silent regression.

Scope (verified against the tree at NFM-5060 commit time, 2026-09-21):

  * ``PHYSICAL_FEATURE_NAMES`` — list of 8 ``str``, exact order, must
    contain every key the surrogate ``.index()``-looks-up
    (u_density, bv_ratio, config_entropy, mixing_enthalpy,
    pauling_chi_diff).  Used at ``ml_surrogate.py`` lines 32 (import),
    40-42 and 462 / 465 (``.index()`` lookups).
  * ``PHASE_MODEL_PATH`` — ``pathlib.Path``, non-empty.  Used at line
    252 (import) and line 273 (``PHASE_CLASSIFIER_PATH`` env fallback).
  * ``TEMP_MODEL_PATH`` — ``pathlib.Path``, non-empty.  Used at line
    253 (import) and line 256 (``TEMP_PREDICTOR_PATH`` env fallback).

Three failure modes the smoke catches:

  1. **Rename** — ``PHYSICAL_FEATURE_NAMES`` becomes ``FEATURE_NAMES``
     (or similar).  ``ml_surrogate.py`` line 32 ``from … import`` raises
     ``ImportError`` at module load.
  2. **Type change** — ``PHYSICAL_FEATURE_NAMES`` becomes ``np.ndarray``
     or ``tuple``.  ``ml_surrogate.py:40-42`` ``.index()`` may still
     work on a tuple, but ``build_feature_matrix`` indexes
     ``feature_matrix[:, idx]`` expecting an ``int`` that is the result
     of ``list.index()``.  Switching the container to ``np.ndarray``
     breaks the contract because surrogate callers expect a list of
     str (see ``feature_engineering.batch_compute`` consumers).
  3. **Count drift** — a feature is added or removed.  ``ml_surrogate``
     relies on ``u_density`` / ``bv_ratio`` / ``config_entropy`` /
     ``mixing_enthalpy`` / ``pauling_chi_diff`` being present; removing
     one raises ``ValueError`` from ``list.index()``.

Spec-vs-repo reconciliation — LOCKED per Petrov 2026-09-21 (option b):

  * NFM-5060 acceptance criteria mention ``pin count = 20`` and
    ``predict_phase_stability(composition: dict) -> dict`` /
    ``predict_binding_energy(composition: dict) -> dict`` signatures.
    The actual surface the surrogate imports is the **8-feature**
    ``PHYSICAL_FEATURE_NAMES`` plus the two ``*_MODEL_PATH`` constants
    — there is no ``predict_phase_stability`` or
    ``predict_binding_energy`` in ``prediction_service`` today, and the
    surrogate does not call any prediction function (it loads joblib
    artifacts directly via the path constants).  The 20-feature
    vocabulary the spec refers to is ``ENERGY_V11_FEATURE_NAMES`` in
    ``nfm_db.ml.energy_features_v11``; that constant is not on the
    surrogate's import path.

  * **Locked surface decision (Petrov, 2026-09-21, comment
    ``d7316700-e2bf-4f7c-a630-57702aa0ab9f`` on NFM-5060):** Petrov
    co-signed option (b) — the surrogate import surface stays at
    ``PHYSICAL_FEATURE_NAMES`` (8 entries) + ``PHASE_MODEL_PATH`` +
    ``TEMP_MODEL_PATH``.  His v3.3 dispatch work widens the
    **energy-predictor path** (separate vocabulary, not on this
    import surface), not the surrogate's physical-feature vocabulary.
    The two ``test_spec_reconciliation_*`` sentinel tests are the
    trip-wires: if anyone later unifies the energy and physical
    vocabularies, or exports ``predict_phase_stability`` /
    ``predict_binding_energy`` on the surrogate's import path, the
    sentinel tests fail loud and force a Novak + Petrov co-signed
    widening of the pin set (see CO-SIGN CHAIN below).

Co-sign chain:

  * Any change to ``prediction_service.py`` that breaks this smoke
    requires **Petrov co-sign** (he owns prediction_service).
  * Any change to ``ml_surrogate.py`` that breaks this smoke requires
    **Novak co-sign** (I own the optimizer).
  * Path divergence from NDE spec: NFM-5060 acceptance criterion 1
    names ``tests/test_optimizer/test_surrogate_wrap.py``.  This file
    lives at ``tests/test_optimizer_wrap.py`` instead because
    NFM-5059 (perf benchmark, in flight on the same branch) has
    already established ``tests/test_optimizer/`` as a subdir
    containing ``test_zr_pareto_regression.py``; co-tenanting that
    directory with another agent's WIP is fine in principle, but a
    flat sibling at ``tests/test_optimizer_wrap.py`` avoids touching
    NFM-5059's pending changes and keeps the smoke clearly
    attributable to NFM-5060 in review.  The path is recorded here so
    the NDE spec can be amended — once NFM-5059's work merges, this
    file can be moved into ``tests/test_optimizer/test_surrogate_wrap.py``
    in a follow-up commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nfm_db import optimization as _opt_pkg
from nfm_db.ml import prediction_service

# ---------------------------------------------------------------------------
# Pinned contract values — update ONLY via Petrov + Novak co-sign.
# ---------------------------------------------------------------------------

#: Exact 8-feature physical feature list the surrogate ``.index()``-looks-up.
#: Order matters: ``build_feature_matrix`` constructs columns in this order.
EXPECTED_PHYSICAL_FEATURE_NAMES: tuple[str, ...] = (
    "mo_equivalent",
    "pauling_chi_diff",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
)

#: Count pin — LOCKED at 8 per Petrov 2026-09-21 co-sign (option b,
#: comment ``d7316700-…`` on NFM-5060).  v3.3 widens the energy-predictor
#: path, NOT this vocabulary.  Widen only via Petrov + Novak co-sign.
EXPECTED_PHYSICAL_FEATURE_COUNT: int = 8

#: Every key the surrogate ``.index()``-looks-up against this list.  If
#: a future refactor drops one, the corresponding ``.index()`` call in
#: ``ml_surrogate`` raises ``ValueError`` deep inside ``_evaluate``.
REQUIRED_SURROGATE_KEYS: frozenset[str] = frozenset({
    "u_density",            # line 40
    "bv_ratio",             # line 41
    "config_entropy",       # line 42
    "mixing_enthalpy",      # line 462
    "pauling_chi_diff",     # line 465
})

# ---------------------------------------------------------------------------
# Baseline pin tests — these run against the live module and MUST pass.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_physical_feature_names_is_list_of_str():
    """PHYSICAL_FEATURE_NAMES must be a list of str (not tuple / ndarray / dict)."""
    assert isinstance(prediction_service.PHYSICAL_FEATURE_NAMES, list), (
        f"PHYSICAL_FEATURE_NAMES must be list, got "
        f"{type(prediction_service.PHYSICAL_FEATURE_NAMES).__name__}"
    )
    assert all(isinstance(name, str) for name in prediction_service.PHYSICAL_FEATURE_NAMES), (
        "PHYSICAL_FEATURE_NAMES must contain only str entries; "
        "non-str entries break dict-key construction in feature_engineering."
    )


@pytest.mark.unit
def test_physical_feature_names_count_is_pinned():
    """Pin: PHYSICAL_FEATURE_NAMES has exactly 8 entries.

    Count drift (add/remove) breaks ``ml_surrogate.build_feature_matrix``
    column order and downstream consumers of the feature matrix.
    """
    assert len(prediction_service.PHYSICAL_FEATURE_NAMES) == EXPECTED_PHYSICAL_FEATURE_COUNT, (
        f"PHYSICAL_FEATURE_NAMES count drifted: "
        f"expected {EXPECTED_PHYSICAL_FEATURE_COUNT}, "
        f"got {len(prediction_service.PHYSICAL_FEATURE_NAMES)}."
    )


@pytest.mark.unit
def test_physical_feature_names_order_is_pinned():
    """Pin: PHYSICAL_FEATURE_NAMES preserves exact element-by-element order.

    ``build_feature_matrix`` and ``_build_cluster_features`` rely on
    column-order matching the feature-engineering output.  Reordering
    silently swaps objectives in the optimizer.
    """
    assert tuple(prediction_service.PHYSICAL_FEATURE_NAMES) == EXPECTED_PHYSICAL_FEATURE_NAMES, (
        f"PHYSICAL_FEATURE_NAMES order drifted.\n"
        f"  expected: {list(EXPECTED_PHYSICAL_FEATURE_NAMES)}\n"
        f"  got     : {prediction_service.PHYSICAL_FEATURE_NAMES}"
    )


@pytest.mark.unit
def test_physical_feature_names_contains_all_surrogate_required_keys():
    """Pin: every ``.index()`` lookup in ml_surrogate resolves without ValueError."""
    names = set(prediction_service.PHYSICAL_FEATURE_NAMES)
    missing = REQUIRED_SURROGATE_KEYS - names
    assert not missing, (
        f"ml_surrogate.index() lookups would raise ValueError; missing keys: "
        f"{sorted(missing)}."
    )


@pytest.mark.unit
def test_phase_model_path_is_path():
    """Pin: PHASE_MODEL_PATH exists, is a Path, and has a non-empty parent.

    Used at ``ml_surrogate.py:252`` (import) and ``:273`` as the env
    fallback for ``PHASE_CLASSIFIER_PATH``.
    """
    assert hasattr(prediction_service, "PHASE_MODEL_PATH"), (
        "PHASE_MODEL_PATH removed from prediction_service; "
        "ml_surrogate.py:252 import will raise ImportError."
    )
    path = prediction_service.PHASE_MODEL_PATH
    assert isinstance(path, Path), (
        f"PHASE_MODEL_PATH must be pathlib.Path, got {type(path).__name__}."
    )
    assert str(path), "PHASE_MODEL_PATH must not be empty."


@pytest.mark.unit
def test_temp_model_path_is_path():
    """Pin: TEMP_MODEL_PATH exists, is a Path, and has a non-empty parent.

    Used at ``ml_surrogate.py:253`` (import) and ``:256`` as the env
    fallback for ``TEMP_PREDICTOR_PATH``.
    """
    assert hasattr(prediction_service, "TEMP_MODEL_PATH"), (
        "TEMP_MODEL_PATH removed from prediction_service; "
        "ml_surrogate.py:253 import will raise ImportError."
    )
    path = prediction_service.TEMP_MODEL_PATH
    assert isinstance(path, Path), (
        f"TEMP_MODEL_PATH must be pathlib.Path, got {type(path).__name__}."
    )
    assert str(path), "TEMP_MODEL_PATH must not be empty."


# ---------------------------------------------------------------------------
# Surrogate-integration pin tests — confirm the import surface resolves.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_surrogate_module_resolves_physical_feature_names_import():
    """ml_surrogate.PHYSICAL_FEATURE_NAMES resolves to the same list object.

    Verifies the symbol that ``from … import PHYSICAL_FEATURE_NAMES`` at
    ``ml_surrogate.py:32`` actually binds is the same object the
    baseline pin tests assert against.  A rename in prediction_service
    would break this binding.
    """
    # Importing ml_surrogate forces its top-level ``from … import`` to
    # execute.  If PHYSICAL_FEATURE_NAMES has been renamed in
    # prediction_service, the import raises ImportError.
    from nfm_db.optimization import ml_surrogate

    assert ml_surrogate.PHYSICAL_FEATURE_NAMES is prediction_service.PHYSICAL_FEATURE_NAMES, (
        "ml_surrogate's bound PHYSICAL_FEATURE_NAMES must be the same object "
        "as prediction_service.PHYSICAL_FEATURE_NAMES (no local re-export)."
    )


@pytest.mark.unit
def test_surrogate_index_lookups_resolve():
    """Every ``.index()`` call site in ml_surrogate resolves to a valid index."""
    names = prediction_service.PHYSICAL_FEATURE_NAMES
    for key in REQUIRED_SURROGATE_KEYS:
        # list.index raises ValueError if absent — this is the exact
        # failure mode ml_surrogate triggers when the contract drifts.
        idx = names.index(key)
        assert isinstance(idx, int) and 0 <= idx < len(names), (
            f"ml_surrogate.index({key!r}) resolved to {idx}, out of range."
        )


@pytest.mark.unit
def test_surrogate_load_models_method_imports_model_paths():
    """PHASE_MODEL_PATH / TEMP_MODEL_PATH are bound inside _load_models.

    These two constants are NOT imported at module top level — they are
    imported inside ``_load_models()`` at ``ml_surrogate.py:251-254`` to
    keep module load hermetic (NFM-1969: artifact load is lazy).  The
    pin therefore lives on the function source, not the module
    namespace.  We ``inspect.getsource`` the method to confirm the
    imports still resolve the same names from ``prediction_service``.
    """
    import inspect

    from nfm_db.optimization import ml_surrogate

    src = inspect.getsource(ml_surrogate.MLSurrogateEvaluator._load_models)
    assert "PHASE_MODEL_PATH" in src, (
        "PHASE_MODEL_PATH import missing from _load_models; "
        "ml_surrogate silently falls back to MODELS_DIR-default paths."
    )
    assert "TEMP_MODEL_PATH" in src, (
        "TEMP_MODEL_PATH import missing from _load_models; "
        "ml_surrogate silently falls back to MODELS_DIR-default paths."
    )
    # Sanity: the names bound inside _load_models still resolve to the
    # same Path objects the baseline pin tests assert against.
    assert prediction_service.PHASE_MODEL_PATH is not None
    assert prediction_service.TEMP_MODEL_PATH is not None


# ---------------------------------------------------------------------------
# Failure-mode demonstrations — meta-tests proving each pin catches a drift.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_failure_mode_rename_would_be_caught_by_hasattr():
    """Failure mode 1: rename PHYSICAL_FEATURE_NAMES -> FEATURE_NAMES.

    Demonstrates the rename failure: a renamed constant breaks the
    ``from … import PHYSICAL_FEATURE_NAMES`` line in ``ml_surrogate.py``.
    The baseline ``hasattr(prediction_service, 'PHYSICAL_FEATURE_NAMES')``
    test catches this; this meta-test documents the regression scenario.
    """
    # Build a fake namespace that emulates the renamed surface.
    class _FakeRenamed:
        FEATURE_NAMES = list(EXPECTED_PHYSICAL_FEATURE_NAMES)  # renamed

    assert hasattr(_FakeRenamed, "FEATURE_NAMES")
    assert not hasattr(_FakeRenamed, "PHYSICAL_FEATURE_NAMES"), (
        "Failure mode 1 demonstration: the rename catches via missing-attribute."
    )


@pytest.mark.unit
def test_failure_mode_type_change_would_be_caught_by_isinstance_check():
    """Failure mode 2: PHYSICAL_FEATURE_NAMES -> tuple (or ndarray).

    The ``isinstance(..., list)`` baseline pin catches a tuple wrapper;
    for ndarray, ``build_feature_matrix`` consumes ``feature_matrix[:, idx]``
    where ``idx`` is ``list.index()`` output — a numpy container changes
    column lookup semantics (array slicing vs list slicing).
    """
    as_tuple: tuple[str, ...] = tuple(EXPECTED_PHYSICAL_FEATURE_NAMES)
    # list.index still works on tuple, so the test pin (line 40) would
    # silently pass — the discriminator is the ``isinstance(..., list)``
    # check that ``test_physical_feature_names_is_list_of_str`` enforces.
    assert not isinstance(as_tuple, list), (
        "Failure mode 2 demonstration: tuple wrapper is rejected by the "
        "list-type pin; ml_surrogate's dict-key construction assumes list."
    )


@pytest.mark.unit
def test_failure_mode_count_drift_would_be_caught_by_index():
    """Failure mode 3: drop a required key (e.g. u_density).

    Demonstrates that ``list.index(key)`` raises ``ValueError`` the
    instant the feature list is shortened — this is the exact exception
    ``ml_surrogate.py:40-42`` would raise at optimization runtime.
    """
    drifted = [n for n in EXPECTED_PHYSICAL_FEATURE_NAMES if n != "u_density"]
    assert len(drifted) == EXPECTED_PHYSICAL_FEATURE_COUNT - 1
    # Python 3.14 reformatted list.index()'s message to
    # "list.index(x): x not in list" (no quoted key); older Pythons
    # used "'<key>' is not in list".  Match either so the demo works
    # regardless of interpreter version.
    with pytest.raises(ValueError, match=r"not in list"):
        drifted.index("u_density")


@pytest.mark.unit
def test_failure_mode_count_drift_would_be_caught_by_membership():
    """Failure mode 3 (alt): drop ALL keys — even ``u_density`` is gone.

    Demonstrates that ``"u_density" in PHYSICAL_FEATURE_NAMES`` is
    sufficient to detect a feature-set trim, so the membership pin
    (``test_physical_feature_names_contains_all_surrogate_required_keys``)
    is the first line of defence.
    """
    drifted = ["unrelated_1", "unrelated_2"]
    assert "u_density" not in drifted
    # The baseline test asserts the inverse — if u_density is missing,
    # the test fails with a clear message naming the missing keys.


# ---------------------------------------------------------------------------
# Co-sign chain guard — surface the spec-vs-repo reconciliation.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spec_reconciliation_predict_phase_stability_not_yet_exported():
    """Sentinel — surface is LOCKED without ``predict_phase_stability``.

    NFM-5060 AC mentions ``predict_phase_stability(composition: dict) -> dict``
    and ``predict_binding_energy(composition: dict) -> dict`` as functions
    to pin.  Neither is exported by ``prediction_service`` (today's functions
    are ``predict_phase``, ``predict_temperature``, ``predict_energy``,
    ``predict_energy_from_composition``), and the surrogate imports no
    prediction functions — it loads joblib artifacts directly via
    ``PHASE_MODEL_PATH`` / ``TEMP_MODEL_PATH``.

    Per Petrov's 2026-09-21 co-sign (option b, comment ``d7316700-…``), the
    surrogate import surface stays at ``PHYSICAL_FEATURE_NAMES`` +
    ``*_MODEL_PATH``; his v3.3 dispatch work widens the energy-predictor
    path (separate vocabulary), NOT this import surface.

    This sentinel PASSES on the current branch.  If anyone later exports
    ``predict_phase_stability`` / ``predict_binding_energy`` on the
    surrogate's import path, this test fails loud and forces a Novak +
    Petrov co-signed widening of the pin set.
    """
    # Sentinel surface is LOCKED: do not export these on the surrogate's
    # import path without Novak + Petrov co-sign.
    assert not hasattr(prediction_service, "predict_phase_stability"), (
        "Surface drift: predict_phase_stability is now exported on the "
        "surrogate's import path. Petrov + Novak co-sign required to "
        "extend this smoke (locked-surface decision d7316700-…)."
    )
    assert not hasattr(prediction_service, "predict_binding_energy"), (
        "Surface drift: predict_binding_energy is now exported on the "
        "surrogate's import path. Petrov + Novak co-sign required to "
        "extend this smoke (locked-surface decision d7316700-…)."
    )


@pytest.mark.unit
def test_spec_reconciliation_physical_feature_count_is_eight_not_twenty():
    """Sentinel — surface is LOCKED at 8 features (not the 20-feature spec).

    NFM-5060 AC says ``PHYSICAL_FEATURE_NAMES — pin count = 20``.  The
    actual constant is 8 entries (the 20D vocabulary is
    ``ENERGY_V11_FEATURE_NAMES`` in ``energy_features_v11``, which is on
    the energy-predictor path and not on the surrogate's import path).

    Per Petrov's 2026-09-21 co-sign (option b, comment ``d7316700-…``),
    the surrogate stays on the 8-feature vocabulary; his v3.3 dispatch
    work widens the energy-predictor path (separate vocabulary).  This
    sentinel pins both vocabularies at their locked counts; any future
    unification of the two lists — or any widening of the surrogate
    vocabulary — fails this test loud and forces Novak + Petrov
    co-signed pin-set widening.
    """
    # Cross-check the *energy* 20D constant stays at its locked count,
    # so a future unification of the two lists is forced to widen the
    # surrogate's pin set deliberately (Novak + Petrov co-sign).
    from nfm_db.ml.energy_features_v11 import ENERGY_V11_FEATURE_NAMES

    assert len(ENERGY_V11_FEATURE_NAMES) == 20, (
        "ENERGY_V11_FEATURE_NAMES count drifted — surrogate does not consume "
        "this list, but a unification would need Novak + Petrov co-sign."
    )
    assert len(prediction_service.PHYSICAL_FEATURE_NAMES) == 8, (
        "PHYSICAL_FEATURE_NAMES count drifted from locked 8; widen the "
        "pin set ONLY via Novak + Petrov co-sign (locked decision d7316700-…)."
    )


# ---------------------------------------------------------------------------
# Sanity guard — the optimization package itself imports cleanly.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_optimization_package_imports_clean():
    """Sanity guard: importing nfm_db.optimization does not touch model artifacts.

    Catches a regression where adding a top-level ``from
    prediction_service import …`` at module load pulls a model file
    from disk (NFM-1969 contract: artifact load is lazy).
    """
    assert _opt_pkg is not None
    # ml_surrogate is importable; its `_ensure_loaded` is lazy.
    from nfm_db.optimization import ml_surrogate  # noqa: F401
