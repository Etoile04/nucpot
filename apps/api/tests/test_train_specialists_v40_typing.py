"""Typing-contract regression test for ``train_specialists_v40.grouped_cv_sidecar``.

NFM-4045 ([SRE] GitHub #1076 backend CI red on commit e331da9):

mypy strict failed with::

    src/nfm_db/ml/train_specialists_v40.py:339: error: Dict entry 3 has
        incompatible type "str": "str"; expected "str": "float"
    src/nfm_db/ml/train_specialists_v40.py:343: error: Dict entry 7 has
        incompatible type "str": "list[dict[str, Any]]"; expected "str": "float"
    src/nfm_db/ml/train_specialists_v40.py:344: error: Dict entry 8 has
        incompatible type "str": "str"; expected "str": "float"

Root cause: ``grouped_cv_sidecar`` declared ``-> dict[str, float]`` but the
returned dict is heterogeneous (``splitter`` / ``preregistration`` strings,
``per_fold_breakdown`` list-of-dicts, plus the float scalars).

This test pins the actual contract: the return value carries the three
heterogeneous keys above with the *correct* value types. If anyone narrows
the return annotation back to ``dict[str, float]`` (or otherwise regresses
the sidecar shape), this test fails because the value type contract is
broken — even if mypy is not running.

Run under ``pytest --noconftest --no-cov`` per repo memory (the conftest
in ``apps/api/tests/`` has a pre-existing import error).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from nfm_db.ml.train_specialists_v40 import grouped_cv_sidecar


def _toy_frame() -> pd.DataFrame:
    """Build a minimal 5-system frame so GroupKFold(n_splits=5) can run.

    ``_parse_composition`` expects a JSON dict string; ``_system`` is the
    element-system grouping key consumed by the sidecar (no need to call
    ``derive_element_system_from_json`` because we pass it directly).
    Five distinct groups are required because ``N_SPLITS = 5``.
    """
    return pd.DataFrame(
        {
            "composition": [
                '{"Mo":0.5,"Zr":0.5}',
                '{"Ti":0.4,"Nb":0.6}',
                '{"Cr":0.5,"Fe":0.5}',
                '{"Al":0.6,"V":0.4}',
                '{"Mn":0.5,"Ru":0.5}',
            ],
            "formation_energy": [-0.42, -0.31, -0.28, -0.19, -0.36],
            "_system": ["Mo-Zr", "Ti-Nb", "Cr-Fe", "Al-V", "Mn-Ru"],
        }
    )


def test_grouped_cv_sidecar_heterogeneous_keys() -> None:
    """Return shape carries the heterogeneous keys mypy complained about."""
    out: dict[str, Any] = grouped_cv_sidecar(_toy_frame(), model_factory=None)

    # Scalars — float / int
    assert isinstance(out["r2_mean"], float)
    assert isinstance(out["r2_std"], float)
    assert isinstance(out["n_folds"], int)
    assert isinstance(out["n_samples"], int)
    assert isinstance(out["n_groups"], int)

    # Heterogeneous keys — the three that triggered NFM-4045's mypy failure
    assert isinstance(out["splitter"], str)
    assert out["splitter"].startswith("GroupKFold")
    assert isinstance(out["per_fold_breakdown"], list)
    assert isinstance(out["per_fold_breakdown"][0], dict)
    assert isinstance(out["preregistration"], str)
    assert "NFM-4031" in out["preregistration"]

    # Float scalars are pure floats, not strings or lists
    for k in ("r2_mean", "r2_std"):
        assert not isinstance(out[k], (str, list, dict)), k


def test_grouped_cv_sidecar_per_fold_shape() -> None:
    """``per_fold_breakdown`` rows have the (fold, n_train, n_test, r2) shape."""
    out: dict[str, Any] = grouped_cv_sidecar(_toy_frame(), model_factory=None)

    rows = out["per_fold_breakdown"]
    assert len(rows) == out["n_folds"]
    for row in rows:
        assert set(row.keys()) == {"fold", "n_train", "n_test", "r2"}
        assert isinstance(row["fold"], int)
        assert isinstance(row["n_train"], int)
        assert isinstance(row["n_test"], int)
        assert isinstance(row["r2"], float)
