"""
nfm-md-runner: Molecular Dynamics Verification Runner for NFMD

Core analysis modules extracted from lammps-automation.

**Modules**:
- DefectAnalyzer: OVITO Wigner-Seitz defect statistics
- DataAverager: Same-condition averaging + IQR/Z-score anomaly detection
- ModelFitter: arc-dpa/RPA fitting + 95% CI

**Architecture**: Zero SQLite coupling, zero SSH coupling.
Business logic fully decoupled from storage/transport layers.
"""

__version__ = "0.1.0"

from nfm_md_runner.defect_analyzer import DefectAnalyzer
from nfm_md_runner.data_averager import DataAverager
from nfm_md_runner.model_fitter import ModelFitter

__all__ = [
    "DefectAnalyzer",
    "DataAverager",
    "ModelFitter",
]
