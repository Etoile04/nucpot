# LAMMPS Diagnostic Workflows

## Overview

Diagnostic workflows provide step-by-step procedures for systematic troubleshooting of LAMMPS failures. Each workflow targets a specific symptom and leads to root cause identification.

## Workflow 1: NaN Error Diagnosis

### When to Use
- NaN appears in thermo output
- Simulation crashes with numerical errors
- Energy or forces go to infinity

### Steps

#### Step 1: Locate NaN Origin
```lammps
# Add custom thermo output to pinpoint NaN source
compute perAtomPE all pe/atoms
compute maxForce all reduce max fx fy fz
thermo_style custom step temp c_perAtomPE c_maxForce
run 0

# Check which compute shows NaN first
```

#### Step 2: Check Initial State
```lammps
# Verify initial configuration
compute 1 all pe
compute 2 all ke
thermo_style custom step c_1 c_2
run 0

# If NaN at step 0: geometry or potential issue
# If NaN after N steps: integration or instability
```

#### Step 3: Test Potential
```lammps
# Create minimal 2-atom test
units metal
atom_style atomic
lattice bcc 3.5
region box block 0 5 0 5 0 5
create_box 1 box
create_atoms 1 box
pair_style eam
pair_coeff * * U.eam U
run 1

# If this fails: potential file issue
# If this works: original script issue
```

#### Step 4: Test Timestep
```lammps
# Try progressively smaller timesteps
timestep 0.001
run 100

timestep 0.0005
run 100

timestep 0.0001
run 100

# Find largest stable timestep
```

#### Step 5: Check for Overlaps
```lammps
# Identify overlapping atoms
compute 1 all property/local batom
dump 1 all local 100 dump.local c_1

# Or use delete_atoms to fix
delete_atoms overlap 0.5 all all
```

### Decision Tree

```
NaN at step 0?
├─ YES → Geometry/Potential issue
│  ├─ Check forces → Very high (>10⁴)? → Bad geometry
│  └─ Test minimal system → Potential file issue
└─ NO → After some steps
   ├─ Reduce timestep until stable
   └─ Check potential at high energy state
```

## Workflow 2: Energy Drift Diagnosis

### When to Use
- Total energy increases monotonically in NVE
- Energy conservation fails
- Temperature increasing unexpectedly in NVE

### Steps

#### Step 1: Measure Energy Drift Rate
```lammps
# Run NVE for test period
fix 1 all nve
variable E0 equal etotal
thermo_style custom step etotal v_E0
run 1000

# Calculate drift rate
# If drift >1% over 10 ps: problem
# If drift <0.01% over 10 ps: acceptable
```

#### Step 2: Check Timestep
```lammps
# Test with half timestep
timestep 0.0005  # Half of original
fix 1 all nve
run 1000

# If drift improves: timestep too large
# If drift unchanged: other issue
```

#### Step 3: Verify Pair Style
```lammps
# Check cutoff appropriateness
neighbor 2.0 bin  # Increase from default

# Test with longer cutoff
pair_style eam 15.0  # Increase cutoff
run 1000

# If drift improves: cutoff issue
```

#### Step 4: Test Different Ensemble
```lammps
# Switch to NVT to see if drift is ensemble-related
fix 1 all nvt temp 300 300 0.1
run 1000

# If drift stops: NVE integration issue
# If drift continues: potential issue
```

#### Step 5: Check Potential Fitting
```lammps
# Is potential appropriate for conditions?
# Check temperature range
# Check material system
# Consult nuclear-materials-knowledge skill

# May need to test different potential
```

### Decision Tree

```
Energy drift in NVE?
├─ Test timestep reduction
│  ├─ Drift improves → Timestep issue
│  └─ Drift unchanged → Continue
├─ Check cutoff
│  ├─ Drift improves → Cutoff issue
│  └─ Drift unchanged → Continue
└─ Test in NVT
   ├─ Drift stops → NVE integration issue
   └─ Drift continues → Potential or fundamental issue
```

## Workflow 3: Lost Atoms Diagnosis

### When to Use
- "Lost atoms" error
- System size decreases during simulation
- Atoms escaping simulation box

### Steps

#### Step 1: Identify Lost Atoms
```lammps
# See which atoms are lost
# (Look at log file for atom IDs)
# Often atoms with high forces are ejected
```

#### Step 2: Check Boundary Conditions
```lammps
# Verify boundary settings
# Print current boundaries
print "Boundary: $(boundary)"

# For bulk: should be p p p
# For surface: should be p p f or f f f
# For nanoparticle: should be f f f
```

#### Step 3: Monitor Forces
```lammps
# Check if very high forces before loss
compute maxForce all reduce max fx fy fz
thermo_style custom step c_maxForce
run 100

# If forces > 1000 eV/Å: problematic
```

#### Step 4: Check Box Size
```lammps
# Is box too small for thermal expansion?
compute vol all vol
variable density equal mass/vol
thermo_style custom step v_density

# If density too high: expand box
```

#### Step 5: Test Thermostat
```lammps
# Use thermostat to limit temperature spikes
fix 1 all nvt temp 300 300 0.1
# or
fix 1 all langevin 300 300 0.1 487848

# Prevents thermal runaway
```

#### Step 6: Use Safety Margin
```lammps
# Create larger box initially
lattice bcc 3.5
region box block -5 15 -5 15 -5 15  # 2× margin

# Or use shrink-wrap
boundary s s s
create_atoms 1 box
```

### Decision Tree

```
Lost atoms error?
├─ Check boundaries → Wrong for simulation type?
│  ├─ Fix: Use correct boundary (p/p/p for bulk)
│  └─ Fix: Use fixed/free for surfaces
├─ Check forces → Very high before loss?
│  └─ Fix: Use thermostat, reduce timestep
└─ Check box size → Too small?
   └─ Fix: Expand box, use shrink-wrap
```

## Workflow 4: Minimization Failure Diagnosis

### When to Use
- Minimization does not converge
- Max force remains high
- Warning: "Minimization did not converge"

### Steps

#### Step 1: Check Initial Forces
```lammps
# Why is minimization difficult?
compute maxForce all reduce max fx fy fz
compute avgForce all reduce ave fx fy fz
thermo_style custom step c_maxForce c_avgForce
run 0

# maxForce > 1000 eV/Å: bad geometry
# maxForce 100-1000 eV/Å: difficult minimization
# maxForce < 100 eV/Å: should converge
```

#### Step 2: Verify Convergence Criteria
```lammps
# Check if tolerances too tight
minimize 1.0e-8 1.0e-10 10000 100000
#                          ^^^^^^  ^^^^^^^
#                          FTol   ETol

# If too tight: relax
minimize 1.0e-4 1.0e-6 1000 10000
```

#### Step 3: Try Different Minimizer
```lammps
# Test different algorithms
min_style cg        # Conjugate gradient
min_style hftn      # HFTN for large systems
min_style sd        # Steepest descent

minimize 1.0e-4 1.0e-6 1000 10000
```

#### Step 4: Remove Constraints
```lammps
# Are constraints preventing relaxation?
# Remove fix setforce
unfix 1  # If fix setforce was applied

# Or relax specific atoms only
group mobile type 1
fix 1 mobile setforce 0.0 0.0 0.0
```

#### Step 5: Improve Initial Guess
```lammps
# Start from better geometry
# Use literature structure
# Use DFT-relaxed structure
# Use relaxed structure from previous run

# Or use soft potential first
pair_style soft 0.5
run 1000
pair_style eam
minimize 1.0e-4 1.0e-6 1000 10000
```

### Decision Tree

```
Minimization fails?
├─ Check initial forces
│  ├─ Forces > 1000 eV/Å → Bad geometry, fix structure
│  └─ Forces < 1000 eVÅ → Continue
├─ Check tolerances
│  └─ Relax if too tight (1e-10 → 1e-6)
├─ Try different min_style
│  ├─ cg → Standard choice
│  ├─ hftn → For large systems
│  └─ sd → Last resort
├─ Check constraints
│  └─ Remove or relax fixes
└─ Improve initial structure
   └─ Use literature/relaxed structure
```

## Workflow 5: Potential File Error Diagnosis

### When to Use
- "Cannot open potential file"
- "Incorrect pair style setting"
- Potential parameters not recognized

### Steps

#### Step 1: Verify File Existence
```bash
# Check file exists
ls -la *.eam *.eam.alloy

# Check file permissions
chmod 644 potential.eam
```

#### Step 2: Check File Format
```bash
# Examine file contents
head -20 potential.eam

# EAM format should have:
# Comment lines (first character: #)
# Element symbols (e.g., U U)
# Number of elements
# Embedding function coefficients
# Pair function coefficients

# EAM/alloy format:
# Different structure for multiple elements
```

#### Step 3: Verify pair_style Match
```lammps
# File extension indicates format:
# .eam → pair_style eam
# .eam.alloy → pair_style eam/alloy
# .eam.fs → pair_style eam/fs

# Test:
pair_style eam/alloy
pair_coeff * * UZr.eam.alloy U Zr
```

#### Step 4: Test with Known Good File
```bash
# Use test potential from LAMMPS distribution
# Or download fresh from NIST IPR

# Test:
pair_style eam
pair_coeff * * Cu_u3.eam Cu  # Known good potential
create_atoms 1 box
run 1
```

#### Step 5: Check Potential Documentation
```
# Review potential paper
# Verify:
# - Elements supported
# - Temperature range
# - Structures tested
# - Known limitations

# Use literature-search skill to find documentation
```

### Decision Tree

```
Potential file error?
├─ File doesn't exist?
│  └─ Download from NIST IPR/OpenKIM
├─ File exists but format error?
│  ├─ Check file extension → pair_style mismatch
│  └─ Check file contents → corrupted?
├─ pair_style error?
│  └─ Match pair_style to file extension
└─ Element not found?
   └─ Verify potential contains all elements
```

## Workflow 6: Thermostat/Barostat Issues

### When to Use
- Temperature oscillates or spikes
- Pressure doesn't converge
- Ensemble doesn't match expectations

### Steps

#### Step 1: Check Temperature Stability
```lammps
# Monitor temperature over time
fix 1 all nvt temp 300 300 0.1
thermo_style custom step temp
run 1000

# Look for:
# - Oscillations >±10%
# - Spikes >2× target
# - Gradual drift
```

#### Step 2: Adjust Damping Parameters
```lammps
# nvt damping (t_T):
# Default: 0.1×timestep
# Try: 0.5×timestep (more aggressive)
fix 1 all nvt temp 300 300 0.5

# or try: 1.0×timestep (less aggressive)
fix 1 all nvt temp 300 300 1.0
```

#### Step 3: Test Alternative Thermostat
```lammps
# Nose-Hoover:
fix 1 all nvt temp 300 300 0.1 0.1

# Langevin:
fix 1 all langevin 300 300 0.1 487848

# Berendsen:
fix 1 all temp/berendsen 300 300 1.0
```

#### Step 4: Check Pressure Control
```lammps
# Monitor pressure
fix 1 all npt temp 300 300 0.1 iso 0.0 0.0 1.0
thermo_style custom step press

# Look for:
# - Oscillations >±50%
# - Not converging to target
# - Negative pressure (expansion)
```

#### Step 5: Adjust Pressure Damping
```lammps
# npt damping (t_p):
# Default: 1.0×timestep
# Try: 0.5×timestep (more aggressive)
fix 1 all npt temp 300 300 0.1 iso 0.0 0.0 0.5

# or try: 2.0×timestep (less aggressive)
fix 1 all npt temp 300 300 0.1 iso 0.0 0.0 2.0
```

#### Step 6: Check System Size
```lammps
# Small systems have larger fluctuations
# Minimum ~1000 atoms for good statistics

variable natoms equal atoms
thermo_style custom step v_natoms press
```

### Decision Tree

```
Ensemble not stabilizing?
├─ Temperature issues?
│  ├─ Adjust damping (0.1 → 0.5 or 1.0)
│  ├─ Try different thermostat (nvt → langevin)
│  └─ Check system size (<1000 atoms?)
├─ Pressure issues?
│  ├─ Adjust damping (1.0 → 0.5 or 2.0)
│  ├─ Try different coupling (iso → aniso)
│  └─ Check for phase transition (real pressure change?)
└─ Both issues?
   └─ Check system size, use larger system
```

## Workflow 7: Performance Issues

### When to Use
- Simulation runs very slowly
- Memory usage too high
- Long wall times

### Steps

#### Step 1: Check Neighbor List Settings
```lammps
# Neighbor list updates
neighbor 2.0 bin every 10

# If updating too often:
neighbor 2.0 bin every 100

# If bin size too small:
neighbor 1.0 bin every 10
```

#### Step 2: Reduce Output Frequency
```lammps
# Thermo output every step is slow
thermo 1000  # Every 1000 steps instead of 1

# Dump output
dump 1 all custom 1000 dump.lammpstrj id type x y z
```

#### Step 3: Check Communication Cutoff
```lammps
# For MPI parallel runs
# Default: communicate every pair style step
# Reduce frequency:
thermo 100
```

#### Step 4: Optimize Pair Style
```lammps
# Some pair styles faster than others
# rank (fastest to slowest):
# 1. eam/alloy (tabulated)
# 2. eam
# 3. buck
# 4. tersoff
# 5. airebo (slowest)

# Use fastest appropriate potential
```

#### Step 5: Reduce System Size
```lammps
# Large systems slow down N² operations
# Test with smaller system first

# If possible:
# - Use smaller simulation box
# - Use coarse-graining
# - Use representative volume
```

### Decision Tree

```
Performance issues?
├─ Neighbor list updates?
│  └─ Reduce update frequency, increase bin size
├─ Output frequency?
│  └─ Reduce thermo/dump frequency
├─ MPI communication?
│  └─ Increase communication cutoff
├─ Pair style?
│  └─ Consider faster potential type
└─ System size?
   └─ Reduce size for testing
```

## General Diagnostic Procedure

### Universal First Steps

1. **Capture full error context**
   - Save complete log file
   - Note exact error messages
   - Identify when error occurs

2. **Create minimal test case**
   - Reduce system to 10-100 atoms
   - Simplify input script
   - Isolate problematic feature

3. **Test components individually**
   - Test potential alone
   - Test thermostat alone
   - Test integrator alone
   - Add back features until failure

4. **Compare to working example**
   - Use known-good input script
   - Swap in problematic components one at a time
   - Identify what breaks it

### Information to Collect

For escalation to human expert, collect:
1. **LAMMPS version**: `lammps -version`
2. **Input script**: Complete script causing error
3. **Log file**: Full output with errors
4. **Potential files**: Which potentials used, sources
5. **System details**: OS, compiler, MPI version
6. **Error reproduction**: Steps to reproduce error
7. **What has been tried**: Previous attempted fixes

### Escape Hatch

If all workflows fail:
1. Check LAMMPS mailing list archives
2. Search for similar issues
3. Verify LAMMPS version (consider update)
4. ESCALATE to human expert
5. Consider alternative simulation method
