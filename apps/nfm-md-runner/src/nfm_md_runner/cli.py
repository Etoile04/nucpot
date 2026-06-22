"""
CLI Interface for nfm-md-runner

Command-line interface for molecular dynamics verification pipeline.
"""

import sys
from pathlib import Path
from typing import Optional

import click
from dotenv import load_dotenv

from .config import settings, verify_environment
from .analysis_manager import AnalysisManager

# Load environment variables
load_dotenv()


@click.group()
@click.version_option(version="0.1.0")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--debug", is_flag=True, help="Enable debug mode")
def cli(verbose: bool = False, debug: bool = False) -> None:
    """
    nfm-md-runner: Molecular Dynamics Verification Runner for NFMD

    **Deployment Scope**: Internal development and testing only
    **Security**: All credentials must be provided via environment variables
    """
    if debug:
        settings.debug = True

    if verbose:
        click.echo(f"nfm-md-runner v{settings.app_version}")
        click.echo(f"Workspace: {settings.workspace_dir}")


@cli.command()
@click.argument("potential_file", type=click.Path(exists=True, path_type=Path))
@click.argument("structure_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output", "-o", type=click.Path(path_type=Path), help="Output file path"
)
@click.option("--temperature", type=float, default=300.0, help="Simulation temperature (K)")
@click.option("--pressure", type=float, default=0.0, help="Simulation pressure (GPa)")
@click.option("--steps", type=int, default=10000, help="Number of MD steps")
@click.option("--fit/--no-fit", default=False, help="Run potential fitting")
def run(
    potential_file: Path,
    structure_file: Path,
    output: Optional[Path],
    temperature: float,
    pressure: float,
    steps: int,
    fit: bool,
) -> None:
    """
    Run MD verification pipeline

    Args:
        potential_file: Path to potential function file
        structure_file: Path to atomic structure file
        output: Optional output file path
        temperature: Simulation temperature (K)
        pressure: Simulation pressure (GPa)
        steps: Number of MD steps
        fit: Whether to run potential fitting
    """
    try:
        # Verify environment configuration
        verify_environment()

        # Create analysis manager
        manager = AnalysisManager()

        # Prepare simulation parameters
        simulation_params = {
            "temperature": temperature,
            "pressure": pressure,
            "steps": steps,
        }

        # Run verification pipeline
        click.echo(f"Running verification for potential: {potential_file.name}")
        results = manager.run_verification_pipeline(
            potential_file=potential_file,
            structure_file=structure_file,
            simulation_params=simulation_params,
            fitting_params={"method": "arc-dpa"} if fit else None,
        )

        # Save results if output path provided
        if output:
            manager.save_results(results, output)
            click.echo(f"Results saved to: {output}")
        else:
            click.echo("Results:")
            click.echo(f"  Timestamp: {results['timestamp']}")
            click.echo(f"  Defect results: {len(results['defect_results'])} analyses")
            if results['fitting_result']:
                click.echo(f"  Fitting: {results['fitting_result'].method}")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("trajectory_file", type=click.Path(exists=True, path_type=Path))
@click.argument("reference_structure", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output JSON file")
def analyze_defects(
    trajectory_file: Path,
    reference_structure: Path,
    output: Optional[Path],
) -> None:
    """
    Analyze defects from MD trajectory

    Args:
        trajectory_file: Path to MD trajectory file
        reference_structure: Path to reference perfect crystal structure
        output: Optional output JSON file
    """
    try:
        verify_environment()

        from .defect_analyzer import DefectAnalyzer

        analyzer = DefectAnalyzer(ovito_python_path=settings.ovito_python_path)
        stats = analyzer.analyze_trajectory(trajectory_file, reference_structure)

        click.echo("Defect Analysis Results:")
        click.echo(f"  Total atoms: {stats.total_atoms}")
        click.echo(f"  Vacancies: {stats.vacancies} ({stats.vacancy_concentration:.4f})")
        click.echo(f"  Interstitials: {stats.interstitials} ({stats.interstitial_concentration:.4f})")

        if output:
            import json

            with open(output, "w") as f:
                json.dump(stats.model_dump(), f, indent=2)
            click.echo(f"Results saved to: {output}")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("potential_file", type=click.Path(exists=True, path_type=Path))
@click.option("--method", type=click.Choice(["arc-dpa", "RPA"]), default="arc-dpa")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output JSON file")
def fit_potential(potential_file: Path, method: str, output: Optional[Path]) -> None:
    """
    Fit potential function parameters

    Args:
        potential_file: Path to potential function file
        method: Fitting method (arc-dpa or RPA)
        output: Optional output JSON file
    """
    try:
        verify_environment()

        from .model_fitter import ModelFitter

        fitter = ModelFitter(fitting_method=method)
        # TODO: Implement actual fitting logic
        click.echo(f"Potential fitting with {method} method")
        click.echo(f"Potential file: {potential_file}")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
def verify_config() -> None:
    """
    Verify environment configuration

    Checks that all required environment variables are set
    and that the security requirements are met.
    """
    try:
        verify_environment()

        click.echo("✅ Environment configuration is valid")
        click.echo(f"  Workspace: {settings.workspace_dir}")
        click.echo(f"  Output: {settings.output_dir}")
        click.echo(f"  Temp: {settings.temp_dir}")

        if settings.hpc_host:
            click.echo(f"  HPC: {settings.hpc_connection_string()}")

        if settings.ovito_enabled:
            click.echo(f"  OVITO: {settings.ovito_python_path}")

    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("directory", type=click.Path(path_type=Path), default=Path.cwd())
def init(directory: Path) -> None:
    """
    Initialize nfm-md-runner workspace

    Creates the necessary directory structure and configuration files.

    Args:
        directory: Directory to initialize (default: current directory)
    """
    try:
        # Create directory structure
        dirs = ["workspace", "output", "temp", "potentials", "structures"]
        for dir_name in dirs:
            (directory / dir_name).mkdir(parents=True, exist_ok=True)

        # Create .env.example file
        env_example = """# nfm-md-runner Configuration

# Application Settings
NFM_DEBUG=false

# HPC Connection Settings (Required for MD execution)
NFM_HPC_HOST=tianjin.nscc.tj.cn
NFM_HPC_PORT=22
NFM_HPC_USER=your_username
NFM_HPC_SSH_KEY_PATH=~/.ssh/id_rsa_tianjin
NFM_HPC_WORK_DIR=/home/your_username/nfm-workspace

# LAMMPS Settings
NFM_LAMMPS_EXECUTABLE=lmp
NFM_LAMMPS_MODULES=intel/19.1.1 impi/2021.4.0

# OVITO Settings (GPL - Internal Use Only)
NFM_OVITO_ENABLED=true
NFM_OVITO_PYTHON_PATH=/path/to/ovito/python
NFM_INTERNAL_USE=true

# SLURM Settings
NFM_SLURM_PARTITION=normal
NFM_SLURM_NODES=1
NFM_SLURM_NTASKS_PER_NODE=32
NFM_SLURM_MAX_ARRAY_SIZE=100

# Database Settings (for Phase 2 integration)
# NFM_DATABASE_URL=postgresql://user:password@localhost/nfmd

# Celery Settings (for Phase 3 integration)
# NFM_CELERY_BROKER_URL=redis://localhost:6379/0
# NFM_CELERY_RESULT_BACKEND=redis://localhost:6379/0
"""

        with open(directory / ".env.example", "w") as f:
            f.write(env_example)

        # Create README.md
        readme = """# nfm-md-runner Workspace

This directory contains the nfm-md-runner workspace for MD verification.

## Directory Structure

- `workspace/`: Working directory for MD simulations
- `output/`: Output results and analysis
- `temp/`: Temporary files
- `potentials/`: Potential function files
- `structures/`: Atomic structure files

## Configuration

Copy `.env.example` to `.env` and configure your environment:

```bash
cp .env.example .env
# Edit .env with your settings
```

## Usage

Run verification pipeline:

```bash
nfm-md run potentials/U-Zr.eam.alloy structures/U-Zr.xsf
```

## Security Notes

- Never commit `.env` file to version control
- Use SSH key authentication for HPC (not passwords)
- OVITO is GPL licensed for internal use only
"""

        with open(directory / "README.md", "w") as f:
            f.write(readme)

        click.echo(f"✅ Initialized nfm-md-runner workspace in {directory}")
        click.echo("  Next steps:")
        click.echo("    1. Copy .env.example to .env")
        click.echo("    2. Configure your HPC credentials")
        click.echo("    3. Run: nfm-md verify-config")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def main() -> None:
    """Entry point for nfm-md CLI"""
    cli()


if __name__ == "__main__":
    main()
