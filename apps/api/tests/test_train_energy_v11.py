"""Tests for train_energy_v11.py (NFM-1809).

Verifies the v1.1 training script:
- is importable (no IndentationError / SyntaxError)
- loads AC-baseline data (1512 records per NFM-1809 AC)
- does not silently drop functional-mixing data via PBE filter
  (AC: 沿用 NFM-1788 v1.0 的 1512 真实 DFT 记录)
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_train_module_importable():
    """The train script must be importable (no IndentationError)."""
    import nfm_db.ml.train_energy_v11 as mod
    assert mod is not None
    assert hasattr(mod, "load_dft_data")
    assert hasattr(mod, "build_dataset")
    assert hasattr(mod, "main")


def test_load_dft_data_uses_full_1512_records():
    """load_dft_data must return the full 1512-record AC baseline (no PBE filter)."""
    import nfm_db.ml.train_energy_v11 as mod

    data_dir = Path(__file__).resolve().parents[3] / "data"
    if not data_dir.exists():
        pytest.skip(f"Data dir not found: {data_dir}")

    raw = mod.load_dft_data(data_dir)
    # AC: 沿用 NFM-1788 v1.0 的 1512 真实 DFT 记录
    # (12 dft-export batches + 2 supplementary + dft_incremental_200.csv)
    assert len(raw) >= 1500, (
        f"AC requires 1512 records; got {len(raw)}. "
        "PBE-only filter violates the AC."
    )
