# LAMMPS Error Patterns and Classification

## Error Pattern Classification System

LAMMPS errors are classified into 5 categories based on fix complexity:

**Class A**: Configuration errors (fix in input script)
**Class B**: Geometry errors (fix with minimization)
**Class C**: Potential errors (replace potential)
**Class D**: Integration errors (adjust integration parameters)
**Class E**: System errors (require redesign or escalation)

## Class A: Configuration Errors

### A1: Wrong pair_style/pair_coeff
**Error Messages**:
```
ERROR: Incorrect pair style setting
ERROR: Cannot open pair_style file
ERROR: All pair coeffs are not set
```

**Root Cause**: Mismatch between potential file format and `pair_style`

**Fix**:
```lammps
# Check potential file format (extension indicates type)
# .eam or .eam.alloy → pair_style eam or eam/alloy
# .eam.fs → pair_style eam/fs

# Correct usage:
pair_style eam/alloy
pair_coeff * * UZr.alloy U Zr

# Wrong:
pair_style eam           # Incompatible with alloy file
```

**Verification**: LAMMPS initializes without pair_style errors

---

### A2: Incorrect boundary settings
**Error Messages**:
```
ERROR: Lost atoms: atoms escaped simulation box
ERROR: Boundaries are not supported
```

**Root Cause**: Boundary type incompatible with simulation

**Fix**:
```lammps
# For bulk simulation (periodic)
boundary p p p  # Periodic in all directions

# For surface (non-periodic in z)
boundary p p f  # Free surfaces in z

# For isolated nanoparticle
boundary f f f  # Non-periodic everywhere

# For shrink-wrapped boundaries
boundary s s s  # Shrink to fit content
```

**Verification**: No lost atoms after equilibrium

---

### A3: Timestep too large
**Error Messages**:
```
WARNING: Energy conservation lost (NVE)
NaN in thermo output after X steps
```

**Root Cause**: Integration timestep exceeds stability limit

**Fix**:
```lammps
# Metals: use 1 fs (0.001 ps)
timestep 0.001

# Oxides: use 0.5 fs (0.0005 ps)
timestep 0.0005

# For high-frequency modes: use 0.2 fs
timestep 0.0002
```

**Guideline**: Δt ≤ 0.001 ps for metals, ≤ 0.0005 ps for oxides

**Verification**: NVE energy conservation < 0.01% over 10 ps

---

### A4: Missing thermo output
**Error Messages**:
```
WARNING: Thermo output not yet defined
```

**Root Cause**: Thermo_style not set before run command

**Fix**:
```lammps
# Always define thermo_style before run
thermo_style custom step temp pe ke etotal press
thermo 100

# For custom output, specify computes
compute 1 all pe
compute 2 all ke
thermo_style custom step c_1 c_2
```

**Verification**: Thermo output appears in log file

---

### A5: Incorrect fix syntax
**Error Messages**:
```
ERROR: Unknown fix identifier
ERROR: Illegal fix command
```

**Root Cause**: Fix ID or parameters incorrect

**Fix**:
```lammps
# Common fixes:
fix 1 all nve                              # NVE ensemble
fix 1 all nvt temp 300 300 0.1           # NVT with thermostat
fix 1 all npt temp 300 300 0.1 iso 0 0 1.0  # NPT with pressure control
fix 1 all langevin 300 300 0.1 487848   # Langevin thermostat

# Check documentation for exact syntax
```

**Verification**: Fix is applied without error

## Class B: Geometry Errors

### B1: Atoms too close
**Error Messages**:
```
WARNING: Atoms are too close
NaN in forces immediately
```

**Root Cause**: Initial positions have overlapping atoms

**Fix**:
```lammps
# Solution 1: Use lattice commands
lattice bcc 3.525  # Proper lattice constant
create_atoms 1 box

# Solution 2: Remove overlaps
delete_atoms overlap 0.5 all all

# Solution 3: Minimize first
minimize 1.0e-4 1.0e-6 100 1000

# Solution 4: Relax with soft potential
pair_style soft 0.5  # Temporary soft potential
run 1000
pair_style eam        # Switch to real potential
run 10000
```

**Verification**: Initial forces < 1000 eV/Å after minimization

---

### B2: Wrong lattice constant
**Error Messages**:
```
WARNING: System energy very high
Pressure > 10 GPa
```

**Root Cause**: Lattice parameter incorrect for material/temperature

**Fix**:
```lammps
# Verify lattice constant from literature
# Example: γ-U at 1000 K has a = 3.55 Å (not 3.525 Å)

# Adjust lattice command:
lattice bcc 3.55  # Temperature-corrected value

# Or use variable for thermal expansion:
variable a0 equal 3.525
variable alpha equal 2.0e-5
variable T equal 1000
variable a equal ${a0}*1.0+${alpha}*${T}
lattice bcc $a
```

**Verification**: Reasonable pressure (< few GPa) after minimization

---

### B3: Missing basis atoms
**Error Messages**:
```
ERROR: Too few atoms
WARNING: System under-coordinated
```

**Root Cause**: Crystal structure requires basis atoms not created

**Fix**:
```lammps
# For structures with basis:
# UO₂ (fluorite): U at (0,0,0), O at (0.25,0.25,0.25)
lattice fcc 5.47
region box block 0 5 0 5 0 5
create_box 2 box
create_atoms 1 box
create_atoms 2 box basis 2 2 2

# For hcp with 2-atom basis:
lattice hcp 3.23 5.15
create_atoms 1 box basis 1 1 1
```

**Verification**: Atom count matches expected crystal structure

---

### B4: Incorrect atomic positions
**Error Messages**:
```
WARNING: Large forces in unexpected directions
```

**Root Cause**: Wrong atomic coordinates or space group

**Fix**:
```lammps
# Use literature or databases for correct positions
# ICSD, Materials Project, or published structures

# For UO₂ fluorite:
# U: (0, 0, 0) [4a Wyckoff]
# O: (1/4, 1/4, 1/4) [8c Wyckoff]

# Or read from CIF file:
read_data UO2_structure.cif
```

**Verification**: Structure matches expected space group

## Class C: Potential Errors

### C1: Potential outside calibrated range
**Error Messages**:
```
WARNING: Potential used outside fitted range
Energy drift in NVE
```

**Root Cause**: Simulation conditions exceed potential calibration

**Fix**:
```lammps
# Check potential documentation for valid range
# If potential fitted to 300-800 K, do not use at 1500 K

# Solutions:
# 1. Use different potential
# 2. Restrict simulation temperature
# 3. Add uncertainty to results

# Example: α-U potential valid < 942 K
# Don't use for γ-U phase (> 1048 K)
```

**Verification**: Temperature within potential calibration range

---

### C2: Missing cross-potentials
**Error Messages**```
ERROR: Pair coeff for all pairs not set
WARNING: Potential parameters missing
```

**Root Cause**: Alloy requires cross-potential that doesn't exist

**Fix**:
```lammps
# Check if U-Zr cross-potential available
# If not, options:
# 1. Use pure element potentials separately (not ideal)
# 2. Find U-Zr specific alloy potential
# 3. Use literature-search skill to find appropriate potential

# Example of correct alloy potential:
pair_style eam/alloy
pair_coeff * * UZr.alloy U Zr  # Must contain U-Zr cross terms
```

**Verification**: All atom pairs have defined interactions

---

### C3: Potential parameters unphysical
**Error Messages**:
```
WARNING: Potential energy values extreme
System energy too high/low
```

**Root Cause**: Corrupted potential file or wrong file used

**Fix**:
```lammps
# Verify potential file contents:
# Check file not empty
# Check parameter values reasonable
# Compare to source documentation

# Download fresh from NIST IPR or OpenKIM
# Use literature-search to verify parameter source

# Test with simple system:
# Create 2-atom system, check interaction energy
```

**Verification**: Per-atom energy reasonable (-3 to -10 eV typical for metals)

## Class D: Integration Errors

### D1: Energy conservation failure (NVE)
**Error Messages**:
```
WARNING: Energy conservation lost
Total energy increasing in NVE
```

**Root Cause**: Timestep too large or integrator inappropriate

**Fix**:
```lammps
# Solution 1: Reduce timestep
timestep 0.0005  # Reduce from 0.001

# Solution 2: Use better integrator
fix 1 all nve
# vs. fix 1 all nve/limit 0.01  # For very small timesteps

# Solution 3: Check for other fixes
remove fixes that modify energy (except thermostat)
```

**Test**: Run NVE for 10 ps, monitor energy drift < 0.01%

---

### D2: Temperature instability (NVT)
**Error Messages**:
```
WARNING: Temperature spikes
Temperature oscillating wildly
```

**Root Cause**: Thermostat parameters too aggressive or wrong type

**Fix**:
```lammps
# Solution 1: Adjust thermostat damping
fix 1 all nvt temp 300 300 0.1  # Increase damping

# Solution 2: Use different thermostat
fix 1 all langevin 300 300 0.1 487848  # Langevin with Tdamp

# Solution 3: Use Nose-Hoover
fix 1 all nvt temp 300 300 0.1 0.1

# Avoid: fix temp/rescale (can cause spikes)
```

**Verification**: Temperature stable ±1% of target

---

### D3: Pressure control issues (NPT)
**Error Messages**:
```
WARNING: Pressure not converging
Box size fluctuating wildly
```

**Root Cause**: Barostat parameters incorrect

**Fix**:
```lammps
# Solution 1: Adjust damping
fix 1 all npt temp 300 300 0.1 iso 0.0 0.0 0.5

# Solution 2: Use anisotropic coupling
fix 1 all npt temp 300 300 0.1 aniso 0.0 0.0 1.0

# Solution 3: Check for phase transition effects
# Box deformation may be real (e.g., melting)
```

**Verification**: Pressure converges to target ±5%

---

## Class E: System Errors

### E1: Fundamental incompatibility
**Error Messages**:
```
No specific error, but results completely wrong
```

**Root Cause**: Potential type fundamentally wrong for material class

**Example**: Using Buckingham potential for pure metal (U)

**Fix**: ESCALATE - Requires domain expert decision
- Consult nuclear-materials-knowledge skill
- Use literature-search to find appropriate potential
- May need different simulation method (DFT instead of MD)

---

### E2: Phase transition not handled
**Error Messages**:
```
Sudden property changes during simulation
Energy spikes at specific temperature
```

**Root Cause**: Simulation crossing phase transition without proper handling

**Fix**:
```lammps
# Solutions:
# 1. Restrict simulation to single-phase region
# 2. Use two different potentials for two phases
# 3. Account for latent heat (not automatic)
# 4. ESCALATE if complex phase behavior

# Example: α-Zr → β-Zr at 1135 K
# Run α-Zr potential below 1135 K
# Switch to β-Zr potential above 1135 K
```

**Verification**: Understandable phase behavior in results

---

### E3: Unknown error patterns
**Characteristics**: Error not in classification, unusual symptoms

**Approach**:
1. Check log file for all warnings and errors
2. Try minimal test case
3. Test with simpler potential
4. ESCALATE to human expert if unresolved

**What to collect for escalation**:
- Complete input script
- Full log file
- LAMMPS version
- System details (OS, compiler)
- Potential files used

## Quick Diagnosis Flowchart

```
┌─────────────────────────────────────────────┐
│ LAMMPS Error Occurred                         │
└────────────────────┬────────────────────────┘
                     │
                     v
         ┌───────────────────────────┐
         │ Is error message explicit?  │
         └─────────────┬─────────────┘
                      │
           ┌──────────┴──────────┐
           │                       │
          YES                    NO
           │                       │
           v                       v
┌─────────────────┐    ┌──────────────────────┐
│ Check error class │    │ Examine log file     │
│ in this document │    │ Look for warnings    │
└────────┬─────────┘    └──────────┬───────────┘
         │                          │
         v                          v
    Apply class-specific     Identify pattern
    fix from this doc       (compare to this doc)
         │                          │
         v                          v
    Test fix                  Try matching pattern
         │                          │
         └──────────┬───────────────┘
                    │
                    v
         ┌──────────────────┐
         │ Does fix work?   │
         └──────┬───────┘
                │
        ┌─────┴─────┐
        │           │
       YES          NO
        │           │
        v           v
   Success    ESCALATE
```

## Error Message Index

### Numerical Errors
- **NaN**: See "A3: Timestep too large", "B1: Atoms too close"
- **Infinity**: Check potential file, verify units
- **Very large numbers**: See "B2: Wrong lattice constant"

### Memory Errors
- **Out of memory**: Reduce system size, use fewer atoms
- **Segmentation fault**: Check potential file, verify LAMMPS version

### File Errors
- **Cannot open file**: Check file path, verify permissions
- **File format error**: Verify pair_style matches file format

### Convergence Errors
- **Minimization fails**: See "B: Geometry Errors"
- **Pressure not converging**: See "D3: Pressure control issues"

### Runtime Errors
- **Lost atoms**: See "A2: Incorrect boundary settings"
- **Temperature spikes**: See "D2: Temperature instability"
- **Energy drift**: See "D1: Energy conservation failure"

## Diagnostic Commands

### Check Initial State
```lammps
# Run before main simulation
log log.test
compute pe all pe
compute ke all ke
compute forces all reduce max fx fy fz
thermo_style custom step c_pe c_ke c_forces
run 0
```

### Test Energy Conservation
```lammps
fix 1 all nve
variable E0 equal etotal
thermo_style custom step v_E0 etotal
run 10000
# Check if energy constant ±0.01%
```

### Monitor Convergence
```lammps
variable fmax equal c_forces
thermo_style custom step temp v_fmax
minimize 1.0e-8 1.0e-10 10000 100000
# Watch fmax decrease
```

## First Things to Try

Before detailed diagnosis, try these (fix >50% of issues):

1. **Reduce timestep by half**
   ```lammps
   timestep 0.0005  # Half of typical 0.001
   ```

2. **Run minimization first**
   ```lammps
   minimize 1.0e-4 1.0e-6 1000 10000
   ```

3. **Check boundary settings**
   ```lammps
   boundary p p p  # Periodic for bulk
   ```

4. **Verify potential file**
   ```bash
   head -20 potential.eam
   # Check not empty, has reasonable values
   ```

5. **Test with smaller system**
   - Reduce to 10-100 atoms if possible
   - Eliminates system-size effects

If these don't work, proceed to full diagnosis.
