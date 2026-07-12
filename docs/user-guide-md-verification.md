# MD Verification Feature - User Guide

## Overview

The MD (Molecular Dynamics) Verification feature allows you to validate nuclear materials potential functions by running LAMMPS simulations and analyzing the results against experimental data or first-principles calculations.

## Access

- **Admin Dashboard**: Navigate to `/admin/md-verification`
- **Public Dashboard**: Navigate to `/md-verification`

## Features

### 1. Submit Verification Tasks

Submit MD simulation jobs to verify potential functions:

#### Required Fields

- **势函数 ID** (Potential Function ID): Identifier for the potential function to verify
- **元素体系** (Element System): Chemical elements in the system (e.g., U, Pu, U-Pu alloy)
- **势函数文件路径** (Potential File Path): Server path to the potential file (e.g., `/data/potentials/U_U.empirical`)
- **结构文件路径** (Structure File Path): Server path to the structure file (e.g., `/data/structures/BCC_U.cif`)

#### Simulation Parameters

- **温度 (Temperature)**: Simulation temperature in Kelvin (0-10000 K)
- **压力 (Pressure)**: Simulation pressure in GPa (-100 to 100 GPa)
- **模拟时间 (Simulation Time)**: Optional, in picoseconds (1-10000 ps)
- **时间步长 (Timestep)**: Optional, in picoseconds (0.0001-0.01 ps)
- **系综类型 (Ensemble)**: NPT (isothermal-isobaric), NVT (isothermal-isochoric), or NVE (microcanonical)
- **优先级 (Priority)**: Optional, 1-10 (higher = more priority)

#### Example Submission

```
Potential ID: EAM_alloy_U
Element System: U (Uranium)
Phase: BCC (Body-Centered Cubic)
Potential File: /data/potentials/U_U.empirical
Structure File: /data/structures/BCC_U.cif
Temperature: 300 K
Pressure: 0 GPa
Ensemble: NPT
Priority: 5
```

### 2. Monitor Task Status

View and manage your submitted verification tasks:

#### Task List View

- **任务ID**: Unique identifier for tracking
- **势函数ID**: Potential function being verified
- **状态 (Status)**: 
  - `pending` - Task queued, waiting to start
  - `submitted` - Task submitted to HPC scheduler
  - `running` - Simulation in progress
  - `completed` - Simulation finished successfully
  - `failed` - Simulation failed with errors
- **提交时间 (Submitted)**: When task was created
- **操作 (Actions)**: View details, cancel (for active tasks)

#### Real-Time Updates

Task detail pages auto-refresh every 5 seconds for running jobs, showing:
- Current status
- HPC job status (if applicable)
- Progress indicators
- Error messages (if failed)

### 3. View Results

When a task completes, view detailed results across four tabs:

#### Overview Tab

- Task metadata (ID, potential, element system)
- Simulation configuration
- Timeline (submitted → started → completed)
- Final status and any error messages

#### Simulation Tab

- **Thermodynamic Data**: Temperature, pressure, energy evolution
- **Convergence Plots**: Energy convergence over simulation time
- **Simulation Metrics**:
  - Total simulation time (ps)
  - Steps completed
  - Final energy, temperature, pressure

#### Defects Tab

- **Defect Analysis Results**: Identified defects and their properties
- **Defect Types**:
  - Vacancy (空位)
  - Interstitial (间隙)
  - Dislocation (位错)
  - Grain Boundary (晶界)
- **Formation Energies**: Energy cost to create each defect
- **Concentrations**: Defect concentrations in the structure

#### Fitting Tab

- **Potential Fitting Results**: Parameters adjusted to match DFT data
- **Fitting Methods**:
  - ARC-DPA: Accelerated Reference Composition for DPA
  - RPA: Recursive Polynomial Approximation
- **Quality Metrics**: Goodness-of-fit statistics
- **Parameter Values**: Optimized potential parameters

### 4. Download Results

Download raw LAMMPS output files for further analysis:
- Trajectory files (.dump, .xyz)
- Log files (.log)
- Thermodynamic data (.dat)
- Custom output formats

## Best Practices

### 1. File Preparation

- Ensure potential files are in LAMMPS-compatible format
- Structure files should be in CIF or LAMMPS data format
- Verify file paths are absolute server paths
- Check file permissions (readable by simulation service)

### 2. Parameter Selection

**Temperature**: Start with room temperature (300 K) for baseline verification

**Pressure**: 
- 0 GPa for ambient conditions
- Elevated pressures (1-10 GPa) for high-pressure phase validation

**Simulation Time**:
- 50-100 ps for basic properties
- 200-500 ps for defect formation energies
- 1000+ ps for convergence validation

**Ensemble**:
- NPT: For lattice constant, thermal expansion validation
- NVT: For fixed-volume properties (elastic constants)
- NVE: For energy conservation tests

### 3. Task Management

- Start with low priority (1-3) for test runs
- Use high priority (8-10) only for production verification
- Monitor early to catch configuration errors quickly
- Cancel failed tasks promptly to free resources

### 4. Result Interpretation

**Energy Convergence**:
- Look for stable energy plateau in final 20% of simulation
- Large fluctuations may indicate instabilities

**Temperature/Pressure**:
- Compare target vs. actual values
- ±5% deviation is acceptable for most ensembles

**Defect Analysis**:
- Formation energies should match DFT calculations within 10%
- Unusually high energies (>5 eV) may indicate problems

## Troubleshooting

### Task Stuck in "Pending"

- Check HPC cluster status
- Verify file paths are accessible
- Ensure simulation service is running

### Task Failed Immediately

- Verify potential file format is correct
- Check structure file for syntax errors
- Review simulation parameter ranges
- Check error messages in task detail view

### No Results Available

- Simulation may still be processing
- Results files may be corrupted
- Check task status for errors

### Charts Not Displaying

- Ensure browser supports Chart.js
- Check that thermodynamic_data exists
- Verify data format matches expected structure

## API Access

For programmatic access, use the REST API endpoints:

```typescript
import { submitMDVerificationJob, getMDVerificationJob } from '@/lib/md-verification-api'

// Submit a job
const job = await submitMDVerificationJob({
  potential_id: 'EAM_alloy_U',
  element_system: 'U',
  potential_file: '/data/potentials/U_U.empirical',
  structure_file: '/data/structures/BCC_U.cif',
  config: {
    temperature: 300,
    pressure: 0,
    ensemble: 'NPT'
  }
})

// Get job status
const status = await getMDVerificationJobStatus(job.id)
```

## Support

For issues or questions:
- Check the [API Integration Guide](./api-integration-md-verification.md)
- Review [LAMMPS Documentation](https://docs.lammps.org/)
- Contact the NFMD team through the admin dashboard feedback form
