---
name: template-selector
description: LAMMPS input template selection and generation for nuclear materials. Maps material properties to potential types (EAM, Buckingham, Tersoff, AIREBO), generates templates for non-EAM systems, and estimates simulation parameters. Use when designing LAMMPS input scripts for nuclear materials.
---

<objective>
Provides template selection logic and parameter estimation for LAMMPS simulations of nuclear materials systems.
</objective>

<domain_knowledge_sources>
For detailed mapping rules and parameter estimation, see:
- [references/property-potential-mapping.md](references/property-potential-mapping.md) - Material properties to potential type selection logic
- [references/non-eam-templates.md](references/non-eam-templates.md) - Template patterns for Buckingham, Tersoff, AIREBO
- [references/parameter-estimation.md](references/parameter-estimation.md) - Convergence criteria, timestep, ensemble selection
</domain_knowledge_sources>

<quick_reference>
<potential_selection_rules>
**For Metals and Alloys (U, Zr, Fe, U-Zr)**:
- Default: EAM (Embedded Atom Method)
- Reason: Metallic bonding, well-established potentials
- When to use: Structure optimization, thermodynamic properties

**For Ionic Systems (UO2, UN)**:
- Default: Buckingham + Coulombic
- Reason: Long-range electrostatic interactions
- When to use: Oxide systems, charged species
- Critical: Must include PPPM for long-range electrostatics

**For Covalent Systems ( Carbides, some alloys)**:
- Default: Tersoff or AIREBO
- Reason: Directional bonding, bond-order effects
- When to use: UC, SiC, mixed covalent-metallic systems

**For Mixed Bonding**:
- Default: Hybrid potential (EAM + Buckingham)
- Reason: Captures metallic + ionic/covalent contributions
- When to use: U-Zr-O systems, fuel-clad interfaces
</potential_selection_rules>

<parameter_estimation_guidelines>
**Timestep Selection**:
- Metals (EAM): 1.0 fs - 2.0 fs
- Oxides (Buckingham): 0.5 fs - 1.0 fs (stiff bonds)
- Covalent (Tersoff): 0.25 fs - 0.5 fs (very stiff)
- Safety margin: Start at 0.5x typical value, increase if stable

**Convergence Criteria**:
- Energy tolerance: 1.0e-4 to 1.0e-6 eV/atom
- Force tolerance: 1.0e-4 to 1.0e-6 eV/Å
- Pressure tolerance: 0.1 to 10.0 kbar

**Ensemble Selection**:
- NVE: Quick testing, energy conservation validation
- NPT: Equilibrium structure optimization, thermal expansion
- NVT: Melting point determination, fixed-volume properties
- Thermodynamic integration: Free energy calculations

**Cutoff Selection**:
- EAM: 2.0x lattice parameter (typical 5-10 Å)
- Buckingham: 10-12 Å (long-range)
- Tersoff: 3.0x bond length (typical 6-8 Å)
- Always: Test convergence with respect to cutoff
</parameter_estimation_guidelines>

<template_generation_workflow>
1. Identify material system (metal/oxide/carbide/alloy)
2. Select potential type based on bonding character
3. Choose appropriate template from references/
4. Adjust parameters (timestep, cutoff, convergence)
5. Add minimization + equilibration + production stages
6. Include thermo output and dump customizations
</template_generation_workflow>

<common_mistakes>
- Using EAM for oxides without long-range electrostatics
- Timestep too large for stiff covalent bonds
- Missing cutoff convergence tests
- Forgetting PPPM with Buckingham+Coulomb
- Incorrect neighbor list update frequency
- Missing energy minimization before dynamics
</common_mistakes>

<when_to_use_this_skill>
Use this skill when:
- Starting a new LAMMPS simulation for nuclear materials
- Choosing between different potential types
- Estimating initial parameters (timestep, cutoff, tolerance)
- Designing input script structure (minimize → equilibrate → produce)
- Debugging potential-related failures (after consulting lammps-debugger)

Do not use for:
- General LAMMPS syntax errors (see lammps-debugger)
- Material properties lookup (see nuclear-materials-knowledge)
- Literature validation (see literature-search)
</when_to_use_this_skill>

<outputs>
This skill produces:
- Recommended potential type with justification
- Base template file path
- Parameter estimates (timestep, cutoff, tolerances)
- Ensemble selection rationale
- Customization suggestions for specific use cases
</outputs>
