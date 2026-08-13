# nfm-md-runner

Molecular Dynamics Verification Runner for NFMD Potential Function Validation

## Overview

`nfm-md-runner` provides the computational backend for potential function verification through molecular dynamics simulation, defect analysis, and fitting. This package extracts core modules from lammps-automation following the NFM-53 ref-gapfill pattern.

**Deployment Scope**: Internal development and testing only  
**Public Launch**: Requires written author consent  
**Security**: All credentials must be provided via environment variables

## Features

- **MD Simulation**: Execute LAMMPS simulations on HPC clusters
- **Defect Analysis**: Extract vacancy and interstitial statistics using OVITO
- **Data Averaging**: Statistical aggregation across multiple simulation runs
- **Potential Fitting**: arc-dpa and RPA methods for potential optimization
- **HPC Integration**: SLURM job array support for parallel execution

## Installation

### Development Installation

```bash
# Clone repository
git clone https://github.com/nfmd/nfm-md-runner
cd nfm-md-runner

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install with development dependencies
pip install -e ".[dev]"

# Install optional dependencies
pip install -e ".[hpc,analysis,fitting]"
```

### Production Installation

```bash
pip install nfm-md-runner
```

## Configuration

All sensitive credentials must be provided via environment variables:

```bash
# Copy example configuration
cp .env.example .env

# Edit with your settings
# Required: HPC credentials (SSH key authentication only)
NFM_HPC_HOST=tianjin.nscc.tj.cn
NFM_HPC_USER=your_username
NFM_HPC_SSH_KEY_PATH=~/.ssh/id_rsa_tianjin

# Optional: OVITO configuration
NFM_OVITO_ENABLED=true
NFM_OVITO_PYTHON_PATH=/path/to/ovito/python
NFM_INTERNAL_USE=true
```

## Usage

### CLI Commands

```bash
# Verify configuration
nfm-md verify-config

# Run MD verification pipeline
nfm-md run potentials/U-Zr.eam.alloy structures/U-Zr.xsf \
  --temperature 300 \
  --steps 10000 \
  --output results/u-zr-verification.json

# Analyze defects from trajectory
nfm-md analyze-defects trajectory.lammpstrue reference.xsf

# Fit potential parameters
nfm-md fit-potential potentials/U-Zr.eam.alloy --method arc-dpa

# Initialize workspace
nfm-md init /path/to/workspace
```

### Python API

```python
from nfm_md_runner import AnalysisManager, verify_environment

# Verify environment
verify_environment()

# Create analysis manager
manager = AnalysisManager()

# Run verification pipeline
results = manager.run_verification_pipeline(
    potential_file="potentials/U-Zr.eam.alloy",
    structure_file="structures/U-Zr.xsf",
    simulation_params={
        "temperature": 300.0,
        "pressure": 0.0,
        "steps": 10000,
    },
    fitting_params={"method": "arc-dpa"},
)

# Save results
manager.save_results(results, "output/verification-results.json")
```

## Architecture

### Module Structure

```
nfm-md-runner/
├── config.py           # Configuration and environment variables
├── defect_analyzer.py   # OVITO-based defect analysis
├── data_averager.py     # Statistical aggregation
├── model_fitter.py      # arc-dpa and RPA fitting
├── analysis_manager.py  # Pipeline orchestration
└── cli.py              # Command-line interface
```

### Integration Pattern

Following NFM-53 ref-gapfill precedent:
1. Extracted from lammps-automation with hexagonal architecture
2. FastAPI adapter layer → NFMD core
3. Celery/RQ for async execution
4. PostgreSQL for results storage

## HPC Deployment

### Tianjin Supercomputing Center (NSCC-TJ)

**Platform**: 天河系列  
**Job Scheduler**: SLURM  
**Access**: User account required  

**Environment Setup**:
```bash
# Login to Tianjin HPC
ssh <user>@<login-node>

# Run environment check
lscpu                          # CPU architecture
nvidia-smi                     # GPU availability
module avail                   # Available software
sinfo                          # Partition information
df -h $HOME ; quota -s         # Storage quota
```

**Job Submission**:
```bash
# Submit SLURM job array
sbatch submit.sh

# Monitor jobs
squeue -u $USER

# Cancel job
scancel <job_id>
```

## Security Notes

⚠️ **CRITICAL**: 
- Never hardcode credentials in source code
- Use SSH key authentication only (no passwords)
- OVITO is GPL licensed for internal use only
- All secrets must be in environment variables or vault

## Testing

```bash
# Run unit tests
pytest tests/

# Run with coverage
pytest --cov=nfm_md_runner --cov-report=html

# Run specific test
pytest tests/test_defect_analyzer.py
```

## Development

### Code Quality

```bash
# Format code
black src/

# Lint code
ruff check src/

# Type checking
mypy src/
```

### Module Extraction

Four modules are drop-in ready from lammps-automation:
- `defect_analyzer` — OVITO defect extraction
- `data_averager` — Statistical aggregation  
- `model_fitter` — arc-dpa/RPA fitting
- `analysis_manager` — Pipeline orchestration

Two modules need redesign:
- `task_scheduler` — Task queue abstraction
- `lammps_runner` — HPC job submission

## License

MIT License (package)  
OVITO (GPL, internal use only)  

## Authors

CTO, NFMD Project Team

## Acknowledgments

- Original lammps-automation by 浮浮酱/幽浮喵
- NFM-53 ref-gapfill pattern
- Tianjin Supercomputing Center (NSCC-TJ)

## Version

0.1.0 (Phase 1: Standalone Package)