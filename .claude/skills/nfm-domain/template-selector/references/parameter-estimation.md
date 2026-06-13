# LAMMPS Parameter Estimation Guidelines

## Timestep Selection

### Bond Stiffness and Timestep

| Potential Type | Typical Bonds | Stiffness | Recommended Timestep | Maximum Safe Timestep |
|----------------|---------------|-----------|---------------------|----------------------|
| EAM | Metallic bonds | Medium | 1.0 - 2.0 fs | 2.5 fs |
| Buckingham | Ionic bonds | High | 0.5 - 1.0 fs | 1.5 fs |
| Tersoff | Covalent bonds | Very High | 0.25 - 0.5 fs | 0.75 fs |
| AIREBO | Mixed (C-C, C-H) | Medium-High | 0.5 - 1.0 fs | 1.5 fs |

### Timestep Selection Workflow

1. **Start conservative**: Use 0.5x typical value
2. **Test energy conservation**: Run short NVE simulation
3. **Check drift**: Acceptable < 0.01 kcal/mol/ps
4. **Increase gradually**: Double timestep, re-test
5. **Monitor instability**: Sudden energy jumps = too large

### Temperature Dependence

- Higher T → higher velocities → requires smaller timestep
- Rule of thumb: Reduce timestep by 20% for every 500K above 1000K
- Melting simulations: Use timestep 2-3x smaller than normal

## Cutoff Radius Selection

### Potential-Specific Guidelines

| Potential Type | Physical Meaning | Typical Cutoff | Convergence Distance |
|----------------|------------------|----------------|---------------------|
| EAM | Pair + embedding | 5.0 - 10.0 Å | 2.0 × lattice parameter |
| Buckingham | Pair + Coulomb | 10.0 - 15.0 Å | Long-range tail critical |
| Tersoff | Three-body | 6.0 - 8.0 Å | 3.0 × bond length |
| AIREBO | REBO + LJ | 10.0 - 12.0 Å | LJ tail dominates |

### Convergence Testing

```lammps
# Test cutoff convergence
variable        cutoff loop 5 12 1
variable        i index ${cutoff}
label           loop_start
pair_coeff      * * ${cutoff}
run             1000
print           "Cutoff: ${cutoff} Energy: $(pe)"
variable        cutoff equal ${cutoff}+1
jump            SELF loop_start
```

Accept cutoff when energy change < 1.0e-4 eV/atom.

### Long-Range Electrostatics (PPPM)

**Accuracy Settings**:
- 1.0e-4: Quick testing
- 1.0e-5: Default for production
- 1.0e-6: High-precision thermodynamics

**Mesh Settings**:
- Start: 6×6×6 (fast but coarse)
- Test: 8×8×8, 10×10×10
- Converge when force error < 1.0e-4

**Cost vs. Accuracy**:
- Mesh 6: 1× baseline cost
- Mesh 8: ~2.5× cost
- Mesh 10: ~5× cost

## Convergence Criteria

### Minimization

| Property | Loose | Medium | Tight | Very Tight |
|----------|-------|--------|-------|------------|
| Energy (eV/atom) | 1.0e-3 | 1.0e-4 | 1.0e-5 | 1.0e-6 |
| Force (eV/Å) | 1.0e-3 | 1.0e-4 | 1.0e-5 | 1.0e-6 |
| Iterations | 1000 | 5000 | 10000 | 50000 |

**Selection**:
- Pre-equilibration: Loose/Medium
- Structure optimization: Medium/Tight
- Defect calculations: Tight/Very Tight

### Dynamics (Thermostat Convergence)

| Property | Convergence Check | Acceptable Drift |
|----------|-------------------|------------------|
| Temperature | Average ± std dev | < 5% of target |
| Pressure | Average ± std dev | < 10% of target |
| Energy | NVE drift | < 0.01 kcal/mol/ps |

## Ensemble Selection

### When to Use Each Ensemble

**NVE (Microcanonical)**:
- Use: Energy conservation tests, quick testing
- Avoid: Production runs (temperature drifts)
- Fix: `fix 1 all nve`

**NPT (Isothermal-Isobaric)**:
- Use: Structure optimization, thermal expansion, density
- Fix: `fix 1 all npt temp Tstart Tend Tdamp iso Pstart Pend Pdamp`
- Damping: 100-1000 timesteps

**NVT (Canonical)**:
- Use: Fixed-volume properties, melting, diffusion
- Fix: `fix 1 all nvt temp Tstart Tend Tdamp`
- Damping: 100 timesteps

**NPH (Isoenthalpic-Isobaric)**:
- Use: Adiabatic compression, enthalpy minimization
- Fix: `fix 1 all nph iso Pstart Pend Pdamp`

### Thermostat Selection

| Thermostat | Best For | Avoid | Comments |
|------------|----------|-------|----------|
| Nose-Hoover | Equilibrium MD | Fast quenching | Standard choice |
| Berendsen | Equilibration | Production | Not rigorous |
| Langevin | Biomolecules, viscosity | Conservative systems | Adds friction |
| Andersen | Large temp jumps | Most cases | Random velocity rescaling |

## Equilibration Duration

### Minimum Times

| System | Quench | Equilibrate | Production |
|--------|--------|-------------|------------|
| Metal (EAM) | 10 ps | 50-100 ps | 100-500 ps |
| Oxide (Buckingham) | 20 ps | 100-200 ps | 200-1000 ps |
| Covalent (Tersoff) | 5 ps | 50-100 ps | 100-500 ps |

### Convergence Indicators

1. **Temperature stability**: Fluctuations around target, no drift
2. **Pressure stability**: NPT runs settle to target pressure
3. **Energy stability**: No systematic drift in NVE
4. **Structure stability**: MSD growth rate constant

## Special Considerations

### Radiation Damage (Cascade Simulations)

**PKA (Primary Knock-on Atom) Initialization**:
```lammps
# Single high-energy atom
velocity        pkas set 5000.0 0 0 units box
```

**Timestep**: 0.1-0.5 fs (critical for energy conservation)

**Cascades**:
- Quench phase: 0.1 fs, 1-2 ps
- Annealing phase: 0.5-1.0 fs, 10-50 ps
- Analysis phase: 1.0 fs, 100+ ps

### Melting Simulations

**Two-Phase Method** (recommended):
```lammps
# Create solid-liquid interface
# Heat both phases together
# Analyze interface temperature
```

**Single-Phase Method**:
- Heat until MSD diverges
- Check latent heat from energy discontinuity
- Timestep: 1.0 fs (EAM metals)

### Diffusion Calculations

**MSD (Mean Squared Displacement)**:
```lammps
compute         msd all msd
compute         diffusion all slope/c_msd[4]
variable       diff equal c_diffusion[3]
fix             out all print 100 "${diff}" file diffusion.txt
```

**Required duration**: Until MSD slope is linear (typically 100-500 ps)

## Cost Optimization

### Neighbor List Updates

```lammps
neighbor        2.0 bin          # 2x skin
neigh_modify     every 10 delay 0 check yes  # Update every 10 steps
```

- Skin: 2.0 Å (safe default)
- Update frequency: 10-20 steps (balance accuracy/cost)

### Output Frequency

```lammps
thermo          100             # Print every 100 steps
dump            1 all custom 1000 traj.lammpstrj ...
```

- Thermo: Every 100-1000 steps (more frequent = slower)
- Dump: Every 1000-10000 steps (disk I/O bottleneck)

## Troubleshooting

| Symptom | Likely Parameter Issue | Fix |
|---------|------------------------|-----|
| Energy exploding | Timestep too large | Reduce timestep 50% |
| Instability at start | Bad contacts | Run minimization |
| Poor convergence | Cutoff too small | Increase cutoff |
| Slow drift in NVE | Thermostat artifacts | Check thermostat settings |
| Unreasonable structure | Wrong potential type | Reconsider potential choice |

## Validation Checklist

Before production run:
- [ ] Timestep tested for energy conservation (NVE)
- [ ] Cutoff converged (energy change < 1.0e-4 eV/atom)
- [ ] PPPM mesh converged (force error < 1.0e-4)
- [ ] Minimization achieved target tolerance
- [ ] Equilibration stabilized T and P
- [ ] Output frequency appropriate for data needs

## Cross-References

- See property-potential-mapping.md for potential selection
- See non-eam-templates.md for example input scripts
- See nuclear-materials-knowledge for material parameters
