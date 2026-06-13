# LAMMPS Failure Patterns and Fixes

## 1. NaN (Not a Number) Propagation

### Classification
- **Severity**: CRITICAL (simulation halts)
- **Frequency**: Common in poorly-initialized systems
- **Root Causes**: Energy/force overflow, unstable integration, bad geometry

### Symptoms
```
Step       Temp          E_pair        E_mol         TotEng         Press     
   0    0              -12345.678      NaN           NaN            NaN     
   1    NaN               NaN           NaN           NaN            NaN     
ERROR: Lost atoms - original 500 current 432
```

### Common Causes

#### 1.1 Atoms Too Close
- **Cause**: Initial positions have overlapping atoms
- **Detection**: Very high initial energy (>10⁶ eV)
- **Fix**: Use `minimize` before dynamics, check `create_atoms`
- **Prevention**: Verify lattice parameters, use `delete_atoms overlap`

#### 1.2 Timestep Too Large
- **Cause**: Integration unstable, energy conservation fails
- **Detection**: NaN after several steps in NVE
- **Fix**: Reduce timestep (0.5ps → 0.1ps for metals)
- **Prevention**: Start with 0.1ps for unknown systems

#### 1.3 Potential Extrapolation
- **Cause**: Atoms closer than potential fitting range
- **Detection**: High forces, force warnings before NaN
- **Fix**: Check pair_style cutoffs, verify potential range
- **Prevention**: Use appropriate cutoff, avoid extreme compression

#### 1.4 Neighbor List Overflow
- **Cause**: Cutoff too short, atoms leaving neighbor shell
- **Detection**: "Neighbor list overflow" warnings
- **Fix**: Increase neighbor list: `neighbor 2.0 bin`
- **Prevention**: Check cutoff vs. thermal motion

### Diagnostic Steps
```lammps
# Check initial energy
compute 1 all pe
compute 2 all ke
thermo_style custom step temp c_1 c_2 press
run 0

# Monitor forces
compute 3 all reduce max fx fy fz
thermo_style custom step c_3

# Check distances
compute 4 all property/local bath
dump 1 all custom 100 dump.lammpstrj id type x y z
```

### Fixes
```lammps
# Fix 1: Minimize first
minimize 1.0e-4 1.0e-6 100 1000

# Fix 2: Reduce timestep
timestep 0.0001  # Reduced from 0.001

# Fix 3: Better initial structure
delete_atoms overlap 0.5 all all

# Fix 4: Increase neighbor list
neighbor 2.0 bin
```

### Verification
- Run minimization: final energy < initial energy
- Test NVE: total energy constant ±0.01% over 10ps
- Check forces: max force < 1000 eV/Å after minimization

---

## 2. Energy Instability

### Classification
- **Severity**: HIGH (simulation continues but unphysical)
- **Frequency**: Common in mismatched potentials
- **Root Causes**: Potential used outside calibrated range

### Symptoms
```
Step       Temp          E_pair         TotEng         Press     
   0  300.00        -12345.6      -12000.5          100.2     
 100  350.50        -12000.2      -11500.8          500.5     
 200  800.00        -10000.5       -9500.2         5000.0     
 300 5000.00        -5000.00       -4500.00       50000.0     
WARNING: Temperature too high!
```

### Common Causes

#### 2.1 Potential Temperature Mismatch
- **Cause**: Potential fitted to low T, used at high T
- **Detection**: Gradual energy/temperature increase
- **Fix**: Check potential temperature range, use appropriate potential
- **Example**: α-U potential used above 942 K (phase transition)

#### 2.2 Phase Transition Not Handled
- **Cause**: Simulation crosses phase transition without updates
- **Detection**: Sudden property changes, lattice instability
- **Fix**: Use potential valid for both phases, or restart with new phase
- **Example**: α-Zr → β-Zr at 1135 K without potential change

#### 2.3 Cutoff Too Short
- **Cause**: Missing longer-range interactions
- **Detection**: Energy drift, pressure fluctuations
- **Fix**: Increase pair_style cutoff
- **Verification**: Check total energy drift in NVE

#### 2.4 Box Size Too Small
- **Cause**: Atoms interacting with periodic images incorrectly
- **Detection**: Pressure spikes, unphysical behavior
- **Fix**: Increase box size, use `fix npt` for proper pressure
- **Prevention**: Leave >10 Å vacuum for surfaces

### Diagnostic Steps
```lammps
# Monitor energy drift
variable E_tot equal c_1 + c_2
variable E_drift equal (v_E_tot - v_E_tot_0)/v_E_tot_0
thermo_style custom step temp v_E_tot v_E_drift press

# Check pressure
compute press all pressure thermo_temp
thermo_style custom step temp c_1 c_press

# Test energy conservation (NVE)
fix 1 all nve
run 10000
```

### Fixes
```lammps
# Fix 1: Use correct potential temperature range
# Check documentation, switch potential if needed

# Fix 2: Increase cutoff
pair_style eam 15.0  # Increased from default

# Fix 3: Use proper thermostat
fix 1 all nvt temp 300.0 300.0 0.1
fix 2 all temp/berendsen 300.0 300.0 1.0

# Fix 4: Handle phase transitions
# Restart simulation with new lattice/potential at transition
```

### Verification
- NVE energy drift < 0.01% over 10 ps
- NVT temperature stable ±1% around target
- Pressure reasonable for material (< 10 GPa unless intended)

---

## 3. Non-Convergence (Minimization)

### Classification
- **Severity**: MEDIUM (prevents dynamics start)
- **Frequency**: Common in poorly-relaxed structures
- **Root Causes**: Bad geometry, incorrect constraints

### Symptoms
```
minimize ...
Loop 0 of 10 ...
  Step Stag Energetic Max fmax
     0    0     -12345.678    5432.123
   100  100     -12344.987    5431.234
   ...
 10000 10000    -12340.123    5420.345
WARNING: Minimization did not converge
```

### Common Causes

#### 3.1 Bad Initial Geometry
- **Cause**: Atoms too close, wrong lattice constant
- **Detection**: Very high initial forces (>10⁴ eV/Å)
- **Fix**: Verify lattice parameters, use proper crystal structure
- **Prevention**: Start from relaxed structure or DFT-optimized coordinates

#### 3.2 Conflicting Constraints
- **Cause**: Fixed atoms + periodic boundary conflicts
- **Detection**: Some atoms have zero force, others high
- **Fix**: Relax unnecessary constraints, use `fix setforce`
- **Example**: Surface atoms fixed while bulk relaxes

#### 3.3 Tolerance Too Tight
- **Cause**: Requested precision beyond machine precision
- **Detection**: Minimization slows, no improvement
- **Fix**: Relax tolerance (1e-6 → 1e-4)
- **Prevention**: Use appropriate tolerances for system size

#### 3.4 Wrong Minimization Algorithm
- **Cause**: Algorithm inappropriate for system
- **Detection**: Slow convergence, oscillations
- **Fix**: Try different `min_style` (cg, hftn, sd)
- **Recommendation**: `cg` (conjugate gradient) for most systems

### Diagnostic Steps
```lammps
# Check force convergence
compute fmax all reduce max fx fy fz
variable fmax_mag equal sqrt(fmax[1]*fmax[1] + fmax[2]*fmax[2] + fmax[3]*fmax[3])
thermo_style custom step c_fmax_mag

# Monitor atom forces
dump 1 all custom 1 forces.dump id type fx fy fz

# Test convergence
min_style cg
minimize 1.0e-4 1.0e-6 100 1000
```

### Fixes
```lammps
# Fix 1: Better initial structure
lattice bcc 3.525
create_atoms 1 box

# Fix 2: Relax constraints
# Remove unnecessary fixes, allow proper relaxation

# Fix 3: Adjust tolerances
minimize 1.0e-4 1.0e-6 1000 10000

# Fix 4: Change algorithm
min_style hftn  # HFTN for large systems
```

### Verification
- Max force < 1.0 eV/Å after minimization
- Energy gradient < 1.0e-4 eV/Å
- No "did not converge" warnings

---

## 4. Lost Atoms

### Classification
- **Severity**: HIGH (system size changes, invalid results)
- **Frequency**: Common in high-energy simulations
- **Root Causes**: Atoms ejected from simulation box

### Symptoms
```
ERROR: Lost atoms: original 1000 current 987
WARNING: System size changed, resetting atoms
```

### Common Causes

#### 4.1 Boundary Conditions Wrong
- **Cause**: `boundary` incorrect for simulation type
- **Detection**: Atoms leave box, periodic images incorrect
- **Fix**: Verify `boundary` setting (p, f, s, m)
- **Example**: Surface simulation with `p` instead of `f`

#### 4.2 High Initial Forces
- **Cause**: Bad geometry → atoms ejected during dynamics
- **Detection**: Initial forces >10³ eV/Å
- **Fix**: Minimize before dynamics, reduce timestep
- **Prevention**: Always minimize for unknown structures

#### 4.3 Box Too Small
- **Cause**: Thermal expansion or melting requires larger box
- **Detection**: Pressure very high, atoms hit boundary
- **Fix**: Use `fix npt` for box relaxation, increase initial size
- **Prevention**: Include >20% extra space for unknown systems

#### 4.4 Temperature Spike
- **Cause**: Sudden energy input (e.g., collision, defect formation)
- **Detection**: Temperature spike > 2× target
- **Fix**: Reduce timestep, use temperature limiting
- **Prevention**: Use `fix temp/rescale` or `fix langevin`

### Diagnostic Steps
```lammps
# Monitor atom count
variable Natoms equal atoms
thermo_style custom step temp v_Natoms

# Check boundary conditions
# Print boundary settings
print "Boundary: $(boundary)"

# Monitor box size
compute myVol all vol
thermo_style custom step temp c_myVol

# Track lost atoms
# Look for lost atoms warnings in log
```

### Fixes
```lammps
# Fix 1: Correct boundary
boundary f f f  # Fixed for non-periodic

# Fix 2: Thermostat to prevent spikes
fix 1 all nvt temp 300.0 300.0 0.1

# Fix 3: Larger box
# Increase initial box by 20-30%

# Fix 4: Temperature limiting
fix 2 all temp/rescale 100 300 300 1.0

# Fix 5: Use fix npt for pressure control
fix 3 all npt temp 300.0 300.0 0.1 iso 0.0 0.0 1.0
```

### Verification
- No lost atoms warnings after minimization
- Constant atom count during dynamics (unless melting/evaporation intended)
- Pressure reasonable (< few GPa for typical solids)

---

## 5. Potential File Errors

### Classification
- **Severity**: MEDIUM (simulation won't start)
- **Frequency**: Common with new potentials
- **Root Causes**: File format, path, or parameter errors

### Symptoms
```
ERROR: Cannot open EAM potential file U_u3.eam
ERROR: Potential file has incorrect format
ERROR: Incorrect potential parameters
```

### Common Causes

#### 5.1 Wrong File Path
- **Cause**: Potential file not in working directory
- **Detection**: "Cannot open" error
- **Fix**: Copy file to working directory or use full path
- **Prevention**: Use `pair_coeff` with full paths

#### 5.2 Format Mismatch
- **Cause**: `pair_style` doesn't match file format
- **Detection**: "Incorrect format" error
- **Fix**: Match `pair_style` to file format (eam vs eam/alloy vs eam/fs)
- **Example**: Using `pair_style eam` with alloy file

#### 5.3 Missing Elements
- **Cause**: Potential file doesn't contain all needed elements
- **Detection**: "Element X not found" error
- **Fix**: Use correct potential file for all elements
- **Prevention**: Check potential file contents before use

#### 5.4 Parameter Range Errors
- **Cause**: Simulation conditions outside potential range
- **Detection**: Warnings or NaN after start
- **Fix**: Verify potential calibration range
- **Prevention**: Check potential documentation

### Diagnostic Steps
```bash
# Check file exists
ls -l *.eam *.alloy

# Verify file format
head -20 potential_file.eam

# Check elements in file
grep -i element potential_file.alloy
```

### Fixes
```lammps
# Fix 1: Use correct path
pair_coeff ** /full/path/to/potential.eam U Zr

# Fix 2: Match pair_style to format
pair_style eam/alloy  # For .alloy files
pair_style eam/fs      # For .fs files

# Fix 3: Verify elements
# Ensure potential file contains U, Zr for U-Zr simulation

# Fix 4: Check file permissions
chmod 644 potential_file.eam
```

### Verification
- Simulation starts without file errors
- Energy values reasonable (not NaN, not extreme)
- Forces computed correctly
- No format warnings in log file

---

## 6. Integration Instability

### Classification
- **Severity**: HIGH (results unphysical)
- **Frequency**: Common in long simulations
- **Root Causes**: Integrator inappropriate for conditions

### Symptoms
```
Step       Temp          Press     
   0  300.00          100.2     
  10  305.50          105.3     
  20  290.00          98.5     
  30  310.00          102.1     
  ...
WARNING: Energy conservation lost (NVE)
```

### Common Causes

#### 6.1 Timestep Too Large
- **Cause**: Integration errors accumulate
- **Detection**: Energy drift in NVE, temperature oscillations
- **Fix**: Reduce timestep (halve until stable)
- **Guideline**: 0.1 ps for metals, 0.5-1.0 ps for biomolecules

#### 6.2 Wrong Integrator
- **Cause**: `fix nve` vs `fix nh` vs `fix rATTLE`
- **Detection**: Poor energy conservation
- **Fix**: Use appropriate integrator for ensemble
- **Recommendation**: `fix nve` for NVE, `fix nvt` for NVT

#### 6.3 SHAKE/RATTLE Issues
- **Cause**: Constraint tolerance too tight
- **Detection**: Simulation stalls, very slow
- **Fix**: Relax constraint tolerance
- **Prevention**: Use `fix shake` with reasonable tolerances

#### 6.4 Respa Issues
- **Cause**: Multiple timestep integration unstable
- **Detection**: Fast modes unstable, energy drift
- **Fix**: Adjust RESPA levels, reduce outer timestep
- **Prevention**: Test RESPA before production runs

### Diagnostic Steps
```lammps
# Test energy conservation (NVE)
fix 1 all nve
variable E0 equal etotal
variable Edrift equal (etotal-v_E0)/v_E0
thermo_style custom step temp v_E0 v_Edrift

# Monitor integrator
thermo_style custom step temp press etotal

# Check timestep effects
# Run with different timesteps, compare drift
```

### Fixes
```lammps
# Fix 1: Reduce timestep
timestep 0.0001  # Reduced from 0.001

# Fix 2: Use correct integrator
fix 1 all nve         # For NVE
fix 1 all nvt temp 300 300 0.1  # For NVT

# Fix 3: Adjust SHAKE tolerance
fix 2 all shake 0.0001 0.0001 100  # Relaxed tolerance

# Fix 4: Simple RESPA
run_style respa 100 100 100
```

### Verification
- NVE energy drift < 0.01% over 10 ps
- Temperature stable ±1% in NVT
- No integration warnings in log

---

## 7. Pressure and Volume Issues

### Classification
- **Severity**: MEDIUM (incorrect density)
- **Frequency**: Common in NPT simulations
- **Root Causes**: Wrong ensemble parameters

### Symptoms
```
Step       Temp          Press         Volume    
   0  300.00       100000.0      1000.0     
  10  300.50        95000.0      1005.0     
  20  299.00        90000.0      1010.0     
WARNING: Target pressure not reached
```

### Common Causes

#### 7.1 Wrong Target Pressure
- **Cause**: `fix npt` parameters incorrect
- **Detection**: Pressure doesn't converge to target
- **Fix**: Check pressure units (atm vs bar vs Pa)
- **LAMMPS Default**: atm (1 atm = 1.01325 bar)

#### 7.2 Damping Too Strong/Weak
- **Cause**: `fix npt` damping parameter wrong
- **Detection**: Oscillations or slow convergence
- **Fix**: Adjust damping (try 0.1-1.0 ps for metals)
- **Guideline**: `damping = 100 × timestep` is reasonable

#### 7.3 Box Constraints Conflict
- **Cause**: Fixed box + pressure control conflict
- **Detection**: Warnings about box changes
- **Fix**: Remove box constraints or use `fix nph` instead
- **Example**: Can't use `fix npt` with `boundary f f f`

#### 7.4 Anisotropic Systems
- **Cause**: Using isotropic coupling on anisotropic material
- **Detection**: Box deforms unphysically
- **Fix**: Use anisotropic coupling: `fix npt temp ... aniso`
- **Example**: α-U (orthorhombic) needs anisotropic control

### Diagnostic Steps
```lammps
# Monitor pressure components
compute press all pressure thermo_temp
compute pxx all reduce/element xx press
compute pyy all reduce/element yy press
compute pzz all reduce/element zz press
thermo_style custom step temp c_pxx c_pyy c_pzz

# Monitor volume
compute vol all vol
thermo_style custom step temp c_vol

# Check convergence
# Look for pressure approaching target
```

### Fixes
```lammps
# Fix 1: Correct pressure units
fix 1 all npt temp 300 300 0.1 iso 0.0 0.0 1.0
# Target pressure = 0.0 atm

# Fix 2: Adjust damping
fix 1 all npt temp 300 300 0.1 iso 0.0 0.0 0.5
# damping = 0.5 ps

# Fix 3: Anisotropic coupling
fix 1 all npt temp 300 300 0.1 aniso 0.0 0.0 1.0

# Fix 4: Use correct fix
fix 1 all nph iso 0.0 0.0 1.0  # For pressure only (no T control)
```

### Verification
- Pressure converges to target ±5%
- Volume stable (no oscillations)
- Box shape appropriate for crystal system

---

## Diagnostic Checklist

### First Steps (When Something Fails)

1. **Check the Log File**
   - Look for ERROR or WARNING messages
   - Note when errors occur (step number)
   - Check for convergence messages

2. **Verify Initial State**
   - Energy reasonable (not NaN, not extreme)
   - Forces reasonable (< 1000 eV/Å after minimization)
   - Atom count correct
   - Box size appropriate

3. **Test Basic Operations**
   ```lammps
   # Test energy calculation
   compute pe all pe
   thermo_style custom step c_pe
   run 0
   
   # Test minimization
   minimize 1.0e-4 1.0e-6 100 1000
   
   # Test NVE conservation
   fix 1 all nve
   run 1000
   ```

4. **Systematic Isolation**
   - Remove fixes, add back one at a time
   - Test with simpler potential
   - Reduce system size
   - Test with different timestep

### Information to Collect

When reporting failures:
1. **System Details**: Material, atoms, box size
2. **LAMMPS Version**: `lammps -version`
3. **Input File**: Full input script
4. **Error Message**: Exact error text
5. **Log File**: Full log up to error
6. **Potential Files**: Which potentials used
7. **Platform**: OS, compiler, MPI

---

## Common Fix Sequences

### Fix Sequence for Unknown System
```
1. Verify structure (lattice parameters)
2. Create atoms with proper lattice
3. Choose appropriate potential
4. Minimize (check convergence)
5. Test NVE (energy conservation)
6. Test NVT (temperature stability)
7. Test NPT (pressure stability)
8. Production run
```

### Fix Sequence for NaN
```
1. Check initial forces (too high?)
2. Reduce timestep (0.001 → 0.0001)
3. Minimize first
4. Increase neighbor list cutoff
5. Check potential parameters
6. Verify atom positions (overlaps?)
7. Test with simpler potential
```

### Fix Sequence for Energy Instability
```
1. Check potential temperature range
2. Verify phase (correct potential?)
3. Test energy conservation (NVE)
4. Adjust timestep
5. Check thermostat parameters
6. Verify pressure control
7. Monitor for convergence
```

---

## Prevention Strategies

### Before Production Runs

1. **Test Runs**
   - Minimize → verify convergence
   - NVE → verify energy conservation
   - NVT/NPT → verify stability
   - Short production test → verify outputs

2. **Validation**
   - Compare to known properties (lattice constant)
   - Check elastic moduli if available
   - Verify thermal expansion reasonable
   - Cross-check with literature

3. **Monitoring**
   - Use thermo output frequently
   - Monitor energy, temperature, pressure
   - Check for warnings in log
   - Save restart files regularly

### Good Practices

1. **Always minimize** before dynamics for unknown structures
2. **Test timestep** with NVE energy conservation
3. **Verify potential** calibration range includes target T
4. **Use appropriate** thermostat/barostat for system
5. **Monitor convergence** during minimization
6. **Save checkpoints** for long runs
7. **Document all** parameters and sources

### Red Flags to Watch

- Energy changes > 10% in single step
- Temperature spikes > 2× target
- Pressure > 10 GPa (unless intended)
- Any NaN values
- Lost atoms warnings
- Convergence failures
- Drift in NVE energy conservation

### When to Escalate

- Unknown error messages
- Fixes don't work after 3 attempts
- System behaves differently from literature
- Potential appears incompatible
- Performance issues (slow convergence)
