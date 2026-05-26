# Technical Feasibility Report: Potential Verification via ASE

## Overview
This report evaluates the feasibility of using the Atomic Simulation Environment (ASE) for the NucPot verification pipeline on Linux.

## ASE Capabilities (Version 3.28.0)
ASE provides a robust framework for manipulating atoms and calculating properties using various calculators.

### Verified Functionality
The following properties were successfully computed using ASE:
- **Lattice Constant**: Determined via Equations of State (EOS).
- **Elastic Constants**: Computed using the strain method (values observed to be reasonable for EMT potentials).
- **Vacancy Formation Energy**: Successfully calculated (e.g., 0.98 eV for Al using EMT).

### KIM API Status
The KIM API (`kimpy`) is currently not available on the target system due to the requirement of native `libkim-api` library builds. However, for the MVP phase, ASE's internal calculators are deemed sufficient.

### Calculator Support
- **EAM Calculator**: The `ase.calculators.eam` module is available and functional for Embedded Atom Method potentials.
- **Future Enhancements**: Implementation of a LAMMPS subprocess wrapper is planned to expand calculator capabilities.

## Nuclear Materials Specifics
Working with nuclear materials often requires:
1. Custom EAM file loading for specialized potentials.
2. Use of LAMMPS as a full calculator backend for higher fidelity.

## Recommendations
- **Simple Potentials**: Use ASE internal calculators for EAM and Lennard-Jones (LJ) potentials.
- **Complex Potentials**: Delegate to a LAMMPS subprocess for Modified Embedded Atom Method (MEAM) or Machine Learning (ML) potentials.
