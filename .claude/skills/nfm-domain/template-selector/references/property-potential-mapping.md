# Property to Potential Type Mapping

## Core Decision Tree

### Material System Classification

**Metals and Metallic Alloys**
- Systems: U, Zr, Fe, U-Zr, U-Fe, Zr-Fe
- Bonding: Metallic (delocalized electrons)
- **Recommended Potential**: EAM (Embedded Atom Method)
- **Rationale**: EAM captures metallic bonding through embedding energy
- **Accuracy**: High for structure, thermodynamics, defects

**Oxides and Ionic Systems**
- Systems: UO2, U3O8, UN, U2N3
- Bonding: Ionic + long-range electrostatics
- **Recommended Potential**: Buckingham + Coulombic (PPPM)
- **Rationale**: Must capture long-range charge interactions
- **Critical**: Require kspace (PPPM) for convergence

**Carbides and Covalent Systems**
- Systems: UC, UN (mixed), ZrC, SiC
- Bonding: Covalent (directional, bond-order)
- **Recommended Potential**: Tersoff or AIREBO
- **Rationale**: Directional bonding requires bond-order formalism
- **Accuracy**: High for structure, elastic properties

**Mixed Bonding Systems**
- Systems: U-Zr-O, fuel-clad interfaces, corrosion layers
- Bonding: Metallic + Ionic/Covalent
- **Recommended Potential**: Hybrid (EAM + Buckingham) or ReaxFF
- **Rationale**: Captures multiple bonding types
- **Complexity**: Higher computational cost, parameter availability limited

## Property-Specific Selection

### Structural Properties
- Lattice constants, phase stability
- **All potentials**: Good (with appropriate parameters)
- **Best**: EAM for metals, Buckingham for oxides

### Thermodynamic Properties
- Enthalpy, entropy, heat capacity
- **EAM**: Excellent for metals
- **Buckingham**: Good for oxides (with proper long-range treatment)
- **Tersoff**: Good for covalent systems

### Mechanical Properties
- Elastic constants, bulk modulus
- **EAM**: Good for metals
- **Buckingham**: Variable for oxides
- **Tersoff**: Excellent for covalent systems

### Defect Properties
- Vacancies, interstitials, surfaces
- **EAM**: Excellent for metals (well-tested)
- **Buckingham**: Moderate for oxides
- **Tersoff**: Good for covalent systems

### Transport Properties
- Diffusion coefficients, conductivity
- **EAM**: Good for metals (requires MD at temperature)
- **Buckingham**: Moderate (limited by computational cost)
- **All**: Require careful ensemble selection and thermostats

## Special Cases

### High-Temperature Simulations
- Systems: Melting, thermal expansion, creep
- **Consider**: Potential temperature dependence
- **EAM**: Generally robust up to melting point
- **Buckingham**: May require re-parameterization for T > 2000K

### Radiation Damage
- Systems: Cascade simulations, defect evolution
- **EAM**: Preferred for metals (fast, reasonable accuracy)
- **All**: Must conserve energy accurately (small timestep)

### Fuel-Clad Interaction
- Systems: UO2-Zr interfaces, chemical mixing
- **Challenge**: Metallic + ionic interface
- **Options**: Hybrid potentials, reactive potentials (ReaxFF), separate phases

## Failure Mode Mapping

| Symptom | Likely Cause | Action |
|---------|--------------|--------|
| Non-conserved energy | Missing long-range electrostatics | Add PPPM for Buckingham |
| Unrealistic forces | Cutoff too small | Increase cutoff radius |
| Poor structure | Wrong potential type | Switch to appropriate formalism |
| Slow convergence | Ensemble mismatch | Change NPT/NVT/NVE |
| Instability at high T | Timestep too large | Reduce timestep |

## Decision Checklist

Before selecting potential:
1. Identify primary bonding type (metallic/ionic/covalent)
2. Check if mixed bonding exists
3. Determine target properties (structure/thermo/mechanical)
4. Verify parameter availability for your system
5. Consider computational cost vs. accuracy
6. Plan convergence tests (cutoff, timestep, kspace)

## Cross-References

- See nuclear-materials-knowledge for potential parameter sources
- See literature-search for validation references
- See parameter-estimation.md for convergence guidelines
