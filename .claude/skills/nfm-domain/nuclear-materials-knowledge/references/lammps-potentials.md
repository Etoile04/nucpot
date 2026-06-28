# LAMMPS Potentials for Nuclear Materials

## EAM (Embedded Atom Method)

### Theory
- **Form**: E = Σᵢ Fᵨ(ρᵢ) + ½ Σᵢⱼ φᵢⱼ(rᵢⱼ)
- **ρᵢ**: Electron density at atom i from neighbors
- **Fᵨ**: Embedding energy (function of local density)
- **φᵢⱼ**: Pair potential between atoms i and j
- **Physical basis**: Captures many-body metallic bonding

### Advantages
- Accurate for metals and alloys
- Computationally efficient
- Many parameter sets available
- Good thermodynamic properties
- Handles defects well

### Disadvantages
- Requires cross-potentials for alloys
- Limited to metallic systems
- Transferability issues outside fitted range
- Poor for ionic/covalent systems

### Available Potentials

#### Uranium (U)
- **Source**: NIST IPR, OpenKIM
- **Types**: 
  - U-U single element EAM
  - Temperature range: up to melting point
  - Accuracy: Good for thermodynamics, elastic properties
- **Use when**: Modeling pure U, γ-U phase
- **LAMMPS**: `pair_style eam` or `pair_style eam/alloy`

#### Zirconium (Zr)
- **Source**: NIST IPR, OpenKIM
- **Types**:
  - Zr-Zr single element EAM
  - Many parameter sets available
  - Temperature range: α and β phases
- **Use when**: Modeling pure Zr, cladding material
- **LAMMPS**: `pair_style eam` or `pair_style eam/alloy`

#### Iron (Fe)
- **Source**: NIST IPR, OpenKIM (many options)
- **Types**:
  - Fe-Fe single element EAM
  - Well-established potentials
  - Magnetic effects in some potentials
- **Use when**: Modeling structural materials, steel components
- **LAMMPS**: `pair_style eam/fs` (for embedded-ion method)

#### U-Zr Alloys
- **Source**: NIST IPR, literature (e.g., Moore et al.)
- **Types**:
  - U-Zr cross potential required
  - Usually developed for specific compositions (U-10%Zr)
  - Temperature range: β phase stability
- **Use when**: Modeling metallic fuels
- **LAMMPS**: `pair_style eam/alloy` with alloy file

### LAMMPS Implementation
```
# Single element EAM
pair_style eam
pair_coeff * * U_u3.eam

# Alloy EAM (multiple elements)
pair_style eam/alloy
pair_coeff * * UZr.alloy U Zr

# Embedded-ion method (Fe with magnetism)
pair_style eam/fs
pair_coeff * * Fe_fs.eam.fs Fe
```

### Best Practices
- Verify potential temperature range before simulation
- Check published accuracy for properties of interest
- Use `compute pe` and `compute ke` to validate energy stability
- Test with `minimize` before dynamics

---

## Buckingham Potential

### Theory
- **Form**: V(r) = A·exp(-r/ρ) - C/r⁶
- **A, ρ, C**: Material-specific parameters
- **Physical basis**: Pauli repulsion + van der Waals attraction

### Advantages
- Good for ionic systems
- Simple form, fast computation
- Works for oxides (UO₂)
- Can combine with other potentials

### Disadvantages
- Poor for metallic bonding
- Limited to short-range interactions
- Requires careful parameterization
- No many-body effects

### UO₂ Specifics
- **Use case**: Uranium dioxide when EAM unavailable
- **Form**: U-U, U-O, O-O Buckingham interactions
- **Parameters**: 
  - U-U: Metallic bonding poorly captured
  - U-O: Most important, ionic-covalent mix
  - O-O: Usually Buckingham or Coulomb
- **Cutoffs**: Typically 10-12 Å
- **Limitations**: Less accurate than EAM for high-T properties

### LAMMPS Implementation
```
# Buckingham with Coulomb for UO₂
pair_style buck/coul/long 12.0
pair_coeff 1 1 U_U.buckingham A_U rho_U C_U
pair_coeff 1 2 U_O.buckingham A_UO rho_UO C_UO
pair_coeff 2 2 O_O.buckingham A_O rho_O C_O

# Add long-range Coulomb
kspace_style pppm 1.0e-4
```

### When to Use Buckingham
- EAM unavailable for UO₂
- Modeling ionic contribution explicitly
- Simple oxide systems
- Quick calculations (less accurate)

### When NOT to Use
- Pure metallic systems (use EAM)
- High accuracy required (use EAM or DFT)
- Systems with mixed bonding types

---

## Tersoff Potential

### Theory
- **Form**: Bond-order dependent potential
- **Physical basis**: Covalent bonding depends on local environment
- **Key feature**: Bond strength depends on coordination

### Advantages
- Excellent for covalent systems
- Captures coordination dependence
- Good for semiconductors (Si, Ge, C)

### Disadvantages
- Complex parameterization
- Limited for metals/oxides
- Not typical for nuclear fuels
- Computationally more expensive

### Nuclear Materials Relevance
- **Use case**: Covalent impurities in fuels
- **Examples**: Carbon in U-Zr, SiC cladding
- **Limited**: Rarely used for U, UO₂, Zr, Fe
- **When needed**: Modeling mixed covalent-metallic systems

### LAMMPS Implementation
```
pair_style tersoff
pair_coeff * * SiC.tersoff Si C
```

### When to Consider
- Modeling SiC cladding
- Carbon impurities in metallic fuel
- Covalent precipitates

---

## AIREBO Potential

### Theory
- **Form**: REBO (reactive empirical bond order) + LJ + torsion
- **Physical basis**: Hydrocarbon chemistry with bond breaking
- **Components**:
  - REBO: Covalent bonding (C-H, C-C, H-H)
  - LJ: Long-range van der Waals
  - Torsion: Dihedral angle dependence

### Advantages
- Excellent for hydrocarbons
- Handles bond formation/breaking
- Realistic chemistry simulation
- Good for organic reactions

### Disadvantages
- Limited to C-H systems
- Computationally expensive
- Overkill for structural properties
- Not applicable to metals/oxides

### Nuclear Materials Relevance
- **Use case**: Fuel-cladding chemical interaction
- **Examples**: 
  - Hydrocarbon reactions with cladding
  - Carbon deposition on surfaces
  - Organic coolant chemistry
- **Limited**: Rarely needed for fuel bulk properties
- **When needed**: Modeling chemical reactions at interfaces

### LAMMPS Implementation
```
pair_style airebo 3.0 1 1
pair_coeff * * CH.airebo C H
```

### When to Use
- Modeling chemical reactions
- Hydrocarbon interactions with surfaces
- Coolant chemistry effects
- Bond-breaking studies

---

## Potential Selection Decision Tree

```
What is your material system?
├─ Pure metal (U, Zr, Fe)
│  └─ Use EAM (first choice)
├─ Alloy (U-Zr, Fe-Cr)
│  └─ Use EAM/alloy with cross-potentials
├─ Oxide (UO₂)
│  ├─ EAM available → Use EAM
│  └─ EAM unavailable → Use Buckingham + Coulomb
├─ Covalent (SiC, impurities)
│  └─ Use Tersoff
└─ Hydrocarbon (coolant, chemistry)
   └─ Use AIREBO
```

---

## Temperature Range Considerations

### EAM Potentials
- **U**: Valid up to 1500 K (some to melting)
- **Zr**: Valid for both α and β phases
- **Fe**: Valid across α-γ-δ transitions (with magnetic effects)
- **U-Zr**: Valid for β phase (>900 K)

### Buckingham Potentials
- **UO₂**: Valid up to 3000 K (melting)
- **Limitation**: Parameters fitted to specific T range
- **Check**: Original publication for valid range

### General Rules
- Always check potential documentation
- Test at target temperature first
- Verify properties (lattice constant, elastic moduli)
- Extrapolation outside fitted range = unreliable results

---

## Accuracy vs. Computational Cost

| Potential Type | Accuracy | Speed | Memory | Use Case |
|---------------|----------|-------|--------|----------|
| EAM | High (metals) | Fast | Low | Pure metals, alloys |
| EAM/alloy | High (alloys) | Fast | Low | Multi-component alloys |
| Buckingham | Medium (oxides) | Medium | Low | Ionic oxides |
| Tersoff | High (covalent) | Medium | Low | Semiconductors, SiC |
| AIREBO | High (organics) | Slow | High | Hydrocarbons, chemistry |

---

## Common Pitfalls

### Cutoff Errors
- **Symptom**: "Neighbor list overflow" or "Missing bonds"
- **Cause**: Cutoff too short for potential
- **Fix**: Increase neighbor list cutoff: `neighbor 2.0 bin`
- **Prevention**: Check potential documentation for recommended cutoff

### Parameter File Errors
- **Symptom**: "Cannot open potential file"
- **Cause**: Wrong file path or format
- **Fix**: Verify file in working directory, check format
- **LAMMPS**: Use `pair_coeff * *` with full path if needed

### Energy Drift
- **Symptom**: Energy increases monotonically in NVE
- **Cause**: Cutoff too low, time step too large
- **Fix**: Reduce timestep, increase cutoff
- **Verification**: Run NVE, monitor total energy conservation

### Wrong Phase Behavior
- **Symptom**: Lattice constant wrong for temperature
- **Cause**: Potential fitted to different phase
- **Fix**: Check potential temperature range
- **Prevention**: Verify potential documentation

---

## Verification Checklist

Before running production simulations:
- [ ] Potential source and version documented
- [ ] Temperature range verified
- [ ] Test run with `minimize` passes
- [ ] NVE energy conservation < 0.01% over 10 ps
- [ ] Lattice constant matches expected value
- [ ] Forces are reasonable (no spikes)
- [ ] No neighbor list warnings
- [ ] Cutoff appropriate for potential

---

## References and Data Sources

### Potential Repositories
- **NIST IPR**: https://www.ctcms.nist.gov/potentials/
- **OpenKIM**: https://openkim.org/
- **Materials Project**: https://materialsproject.org/
- **LAMMPS potentials**: https://lammps.sandia.gov/potentials.html

### Literature Sources
- Moore et al. (2015) - U-Zr EAM potentials
- Xiang et al. (2020) - UO₂ Buckingham parameters
- Ackland & Thetford (1987) - Fe EAM (classic)
- Mendelev et al. (2003) - Zr EAM potentials

### Validation Data
- **Experimental**: ICSD (Inorganic Crystal Structure Database)
- **DFT**: Materials Project formation energies
- **Elastic constants**: Literature values for validation
