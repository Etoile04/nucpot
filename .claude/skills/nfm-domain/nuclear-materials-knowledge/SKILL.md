---
name: nuclear-materials-knowledge
description: Domain knowledge base for nuclear materials: U, UO2, Zr, Fe, U-Zr crystal structures, LAMMPS potential types (EAM, Buckingham, Tersoff, AIREBO), and common LAMMPS failure patterns with fixes. Use when retrieving nuclear materials properties, selecting LAMMPS potentials, or diagnosing LAMMPS failures.
---

<objective>
Provides specialized knowledge for nuclear materials simulation with LAMMPS, including crystal structures, potential selection, and failure diagnosis.
</objective>

<domain_knowledge_sources>
For detailed crystal structures and parameters, see:
- [references/crystal-structures.md](references/crystal-structures.md) - U, UO2, Zr, Fe, U-Zr lattice parameters and phases
- [references/lammps-potentials.md](references/lammps-potentials.md) - EAM, Buckingham, Tersoff, AIREBO potential types and use cases
- [references/failure-patterns.md](references/failure-patterns.md) - Common LAMMPS errors and fixes
</domain_knowledge_sources>

<quick_reference>
<material_systems>
**Uranium (U)**:
- Phases: α (orthorhombic), β (tetragonal), γ (BCC)
- Lattice parameters: α-U: a=2.85Å, b=5.87Å, c=4.95Å
- Preferred potential: EAM (U-specific potentials available)

**Uranium Dioxide (UO2)**:
- Structure: Fluorite (CaF2), FCC
- Lattice parameter: a=5.47Å
- Preferred potential: EAM (many UO2-specific potentials)
- Critical: Oxygen stoichiometry affects properties

**Zirconium (Zr)**:
- Phases: α (HCP), β (BCC)
- Transition temperature: 1135K
- Lattice parameters: α-Zr: a=3.23Å, c=5.15Å
- Preferred potential: EAM (Zr-specific potentials)

**Iron (Fe)**:
- Phases: α (BCC), γ (FCC), δ (BCC)
- Transition temperatures: α→γ at 912°C, γ→δ at 1394°C
- Preferred potential: EAM (well-established Fe potentials)

**U-Zr Alloys**:
- Structure: BCC (β phase) at reactor temperatures
- Lattice parameter: varies with Zr concentration (~3.5-3.6Å)
- Preferred potential: EAM (U-Zr cross potentials available)
</material_systems>

<lammps_potentials>
**EAM (Embedded Atom Method)**:
- Best for: Metals and alloys (U, Zr, Fe, U-Zr)
- Accuracy: High for metallic systems
- Temperature range: Up to melting point
- Limitations: Requires parameterization for each element pair
- Use when: Available for your material system

**Buckingham**:
- Best for: Ionic oxides (UO2)
- Form: V(r) = A*exp(-r/ρ) - C/r^6
- Accuracy: Good for short-range ionic interactions
- Limitations: Poor for metallic bonding
- Use when: EAM unavailable for oxides

**Tersoff**:
- Best for: Covalent systems (not typical for nuclear fuels)
- Form: Bond-order dependent
- Accuracy: Good for semiconductors
- Limitations: Complex parameterization
- Use when: Modeling covalent impurities

**AIREBO**:
- Best for: Hydrocarbons and organic systems
- Form: REBO + LJ + torsion
- Accuracy: Excellent for C-H systems
- Use when: Modeling fuel-cladding chemical interactions
</lammps_potentials>

<common_failures>
**NaN (Not a Number) Propagation**:
- Cause: Energy/force overflow, unstable integration
- Symptoms: NaN in thermo output, simulation crashes
- Quick fix: Reduce timestep (0.5ps → 0.1ps), check pair_style parameters
- Root cause: Atoms too close, bad initial configuration
- Prevention: Use `minimize` before dynamics, check neighbor list

**Energy Instability**:
- Cause: Potential mismatch, parameter extrapolation
- Symptoms: Temperature/energy spikes, system explodes
- Quick fix: Reduce timestep, check potential cutoffs
- Root cause: Potential used outside calibrated range
- Prevention: Verify temperature range in potential documentation

**Non-Convergence**:
- Cause: Poor minimization, conflicting constraints
- Symptoms: Minimization doesn't reach tolerance
- Quick fix: Increase max iterations, relax tolerance
- Root cause: Bad initial geometry, incorrect boundary conditions
- Prevention: Start from relaxed structure, verify `boundary` command

**Lost Atoms**:
- Cause: Atoms leave simulation box
- Symptoms: "Lost atoms" error, system size decreases
- Quick fix: Check `boundary` conditions, increase box size
- Root cause: Wrong box dimensions, atoms ejected by high forces
- Prevention: Use `fix nve` with temperature control, verify initial structure
</common_failures>
</quick_reference>

<workflow>
When retrieving nuclear materials knowledge:

1. **Identify the material system** - Which material(s) are involved?
2. **Check phase and temperature** - What phase is relevant at simulation temperature?
3. **Retrieve lattice parameters** - Use crystal-structures reference
4. **Select appropriate potential** - Use lammps-potentials reference
5. **Check for common failure patterns** - If troubleshooting, see failure-patterns reference

For crystal structure details:
- Read [references/crystal-structures.md](references/crystal-structures.md)
- Extract lattice parameters, atomic positions, space group
- Note temperature-dependent phase transitions

For potential selection:
- Read [references/lammps-potentials.md](references/lammps-potentials.md)
- Match material system to potential type
- Verify temperature range and accuracy
- Check parameter availability

For failure diagnosis:
- Read [references/failure-patterns.md](references/failure-patterns.md)
- Match error symptoms to pattern
- Apply recommended fixes
- Verify solution prevents recurrence
</workflow>

<output_format>
Structure responses as:

**Material**: [name and phase]
**Crystal Structure**: [type, lattice parameters, atomic positions]
**Preferred Potential**: [type with justification]
**Key Parameters**: [cutoffs, timestep recommendations]
**Common Issues**: [specific failure patterns for this material]

For failures:
**Error Type**: [classification]
**Likely Cause**: [root cause]
**Recommended Fix**: [concrete steps]
**Prevention**: [how to avoid recurrence]
</output_format>

<validation>
A complete nuclear materials knowledge response includes:
- Correct crystal structure for the specified phase and temperature
- Appropriate LAMMPS potential type with justification
- Specific parameter values (lattice constants, cutoffs)
- Recognition of failure patterns when present
- Actionable troubleshooting steps with expected outcomes

Never:
- Recommend EAM for ionic systems without cross-potentials
- Suggest potentials outside their calibrated temperature range
- Ignore phase transitions in temperature calculations
- Provide troubleshooting without identifying root cause
</validation>

<integration_notes>
This skill works with other nuclear domain skills:
- **literature-search** - Validates potential parameters against literature
- **lammps-debugger** - Provides domain knowledge for failure analysis
- **template-selector** - Supplies material parameters for LAMMPS input

Common workflow: literature-search finds potential → nuclear-materials-knowledge validates → template-selector generates input
</integration_notes>
