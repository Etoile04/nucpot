# Fix Validation Procedures

## Overview

Fix validation ensures proposed solutions actually resolve LAMMPS errors and don't introduce new problems. This document provides testing procedures to verify fixes before production use.

## Validation Levels

### Level 1: Basic Validation (Quick Check)
**Purpose**: Verify fix eliminates immediate error
**Time**: 1-5 minutes
**Tests**:
- LAMMPS initializes without error
- Simulation runs for 100-1000 steps
- No NaN or crash

### Level 2: Functional Validation (Standard Check)
**Purpose**: Verify fix maintains physical correctness
**Time**: 10-30 minutes
**Tests**:
- Energy conservation (NVE)
- Temperature stability (NVT)
- Pressure stability (NPT)
- Reasonable property values

### Level 3: Comprehensive Validation (Production Check)
**Purpose**: Verify fix for production use
**Time**: 1-2 hours
**Tests**:
- Extended simulation (10-100 ps)
- Property validation vs literature
- Multiple test conditions
- Performance assessment

## Validation Procedures by Error Type

### NaN Error Validation

#### Level 1: Basic Validation
```lammps
# Test script after fix
timestep 0.0001  # Reduced timestep fix
minimize 1.0e-4 1.0e-6 1000 10000
fix 1 all nve
run 1000

# Expected: No NaN, simulation completes
# Pass: No NaN in output
# Fail: NaN appears anywhere
```

#### Level 2: Functional Validation
```lammps
# Test energy conservation
timestep 0.0001
minimize 1.0e-4 1.0e-6 1000 10000
fix 1 all nve
variable E0 equal etotal
thermo_style custom step etotal v_E0
run 10000

# Check: Energy drift < 0.01% over 10 ps
# Pass: (E_final - E_0) / E_0 < 0.0001
# Fail: Energy drift exceeds 0.01%
```

#### Level 3: Comprehensive Validation
```lammps
# Extended test with properties
timestep 0.0001
minimize 1.0e-4 1.0e-6 1000 10000

# Calculate lattice constant from final box
compute lattice all lattice/offset
# Verify within 1% of literature value

# Run at elevated temperature
fix 1 all nvt temp 800 800 0.1
run 10000

# Check thermal expansion reasonable
# Pass: Lattice expands ~1% at 800 K
# Fail: Unphysical expansion or crash
```

### Energy Drift Validation

#### Level 1: Basic Validation
```lammps
# Test with fix (e.g., reduced timestep)
timestep 0.0005  # Fixed from 0.001
fix 1 all nve
variable E0 equal etotal
thermo_style custom step etotal v_E0
run 1000

# Check: No NaN, energy stable
# Pass: Energy approximately constant
# Fail: Energy still drifting
```

#### Level 2: Functional Validation
```lammps
# Test with fix for extended period
timestep 0.0005
fix 1 all nve
variable E0 equal etotal
thermo_style custom step etotal v_E0
run 10000

# Quantify drift:
# drift = (E_final - E_0) / E_0
# Pass: drift < 0.01 (1%)
# Acceptable: drift 0.01-0.05 (1-5%)
# Fail: drift > 0.05 (5%)
```

#### Level 3: Comprehensive Validation
```lammps
# Test multiple ensembles
# NVE
timestep 0.0005
fix 1 all nve
run 10000

# NVT
fix 1 all nvt temp 300 300 0.1
run 10000

# Check each ensemble
# Pass: All ensembles stable
# Fail: One ensemble still problematic
```

### Lost Atoms Validation

#### Level 1: Basic Validation
```lammps
# Test with fix (e.g., corrected boundary)
boundary p p p  # Fixed from p p f
timestep 0.0001
run 1000

# Check: No lost atoms error
# Pass: Atom count constant
# Fail: Still losing atoms
```

#### Level 2: Functional Validation
```lammps
# Test with thermostat
boundary p p p
timestep 0.0001
fix 1 all nvt temp 300 300 0.1
variable natoms equal atoms
thermo_style custom step v_natoms
run 10000

# Check: Atom count constant
# Pass: natoms constant throughout
# Fail: natoms decreases
```

#### Level 3: Comprehensive Validation
```lammps
# Test temperature ramp
boundary p p p
timestep 0.0001
fix 1 all nvt temp 300 800 0.1
run 10000
fix 2 all nvt temp 800 300 0.1
run 10000

# Check: No atoms lost during heating/cooling
# Pass: Constant atom count
# Fail: Atoms lost during temperature change
```

### Minimization Failure Validation

#### Level 1: Basic Validation
```lammps
# Test with fix (e.g., relaxed tolerances)
minimize 1.0e-4 1.0e-6 1000 10000

# Check: Converges (or reaches iteration limit)
# Pass: "Minimization converged" or reasonable forces
# Fail: Still high forces after max iterations
```

#### Level 2: Functional Validation
```lammps
# Check final force
compute maxForce all reduce max fx fy fz
thermo_style custom step c_maxForce
run 0  # After minimization

# Evaluate: Max force magnitude
# Pass: maxForce < 10 eV/Å (good convergence)
# Acceptable: maxForce < 100 eV/Å (reasonable)
# Fail: maxForce > 100 eV/Å (poor convergence)
```

#### Level 3: Comprehensive Validation
```lammps
# Test minimization then dynamics
minimize 1.0e-4 1.0e-6 1000 10000
fix 1 all nve
timestep 0.0001
run 1000

# Check: Energy stable after minimization
# Pass: Energy constant ±0.1%
# Fail: Energy still drifting significantly
```

### Potential File Error Validation

#### Level 1: Basic Validation
```lammps
# Test with new potential file
pair_style eam/alloy
pair_coeff * * UZr.eam.alloy U Zr
create_atoms 1 box
run 1

# Check: No file errors
# Pass: LAMMPS accepts potential
# Fail: Still "cannot open" or "incorrect format"
```

#### Level 2: Functional Validation
```lammps
# Test potential produces reasonable values
pair_style eam/alloy
pair_coeff * * UZr.eam.alloy U Zr
minimize 1.0e-4 1.0e-6 1000 10000

# Check final energy and forces
compute pe all pe
thermo_style custom step c_pe
run 0

# Evaluate: Per-atom energy
# Pass: pe/atom ≈ -3 to -10 eV (reasonable for metals)
# Acceptable: pe/atom ≈ -2 to -12 eV
# Fail: pe/atom > 0 or < -20 eV (unphysical)
```

#### Level 3: Comprehensive Validation
```lammps
# Test multiple properties
pair_style eam/alloy
pair_coeff * * UZr.eam.alloy U Zr

# Test 1: Lattice constant
minimize 1.0e-4 1.0e-6 1000 10000
compute lattice all lattice
# Compare to literature: Should be within 5%

# Test 2: Elastic properties
displace_atoms small random
fix 1 all box/relax
minimize 1.0e-4 1.0e-6 100 1000
# Extract elastic moduli, compare to DFT/experiment

# Test 3: High temperature
fix 1 all nvt temp 800 800 0.1
run 1000
# Check structure reasonable, no crashes

# Pass: All tests reasonable
# Fail: Any test produces unreasonable results
```

## Validation Test Templates

### Template 1: Basic Stability Test (All Errors)
```lammps
# Basic validation template - use for any fix
# Save as validation_test.lammps

# ===== System Setup =====
units metal
atom_style atomic
boundary p p p
lattice bcc 3.525
region box block 0 10 0 10 0 10
create_box 1 box
create_atoms 1 box

# ===== Potential =====
pair_style eam
pair_coeff * * U.eam U

# ===== Initial Relaxation =====
minimize 1.0e-4 1.0e-6 1000 10000

# ===== Dynamics Test =====
timestep 0.0001
fix 1 all nve
variable E0 equal etotal
thermo_style custom step etotal v_E0
run 5000

# ===== Evaluation =====
# Energy conservation
variable Edrift equal (etotal-v_E0)/v_E0
print "Energy drift: ${Edrift}"

# Expected: Edrift < 0.01 (1%)
# Result: PASS if Edrift < 0.01
```

### Template 2: Thermostat Test
```lammps
# Thermostat validation template
# Save as thermostat_test.lammps

units metal
atom_style atomic
boundary p p p
lattice fcc 5.47
region box block 0 5 0 5 0 5
create_box 1 box
create_atoms 1 box

pair_style eam
pair_coeff * * Cu.eam Cu

minimize 1.0e-4 1.0e-6 1000 10000

timestep 0.001
fix 1 all nvt temp 300 300 0.1
thermo_style custom step temp
run 10000

# Evaluation
# Calculate temperature standard deviation
variable Tave equal ave(temp)
variable Tstd equal std(temp)
print "Temperature: $Tave ± $Tstd"

# Expected: Tstd / Tave < 0.01 (1% fluctuation)
# Result: PASS if Tstd < 3 K
```

### Template 3: Barostat Test
```lammps
# Barostat validation template
# Save as barostat_test.lammps

units metal
atom_style atomic
boundary p p p
lattice bcc 3.525
region box block 0 10 0 10 0 10
create_box 1 box
create_atoms 1 box

pair_style eam
pair_coeff * * U.eam U

minimize 1.0e-4 1.e-6 1000 10000

timestep 0.001
fix 1 all npt temp 300 300 0.1 iso 0.0 0.0 1.0
thermo_style custom step press
run 10000

# Evaluation
variable Pave equal ave(press)
variable Pstd equal std(press)
print "Pressure: $Pave ± $Pstd"

# Expected: Pstd < 10% of Pave (or < 100 MPa for typical)
# Result: PASS if pressure converges to target ±5%
```

## Automated Validation Scripts

### Python Validation Script
```python
#!/usr/bin/env python3
"""
LAMMPS fix validation script
Usage: python3 validate_fix.py input.lammpstrj
"""

import sys
import numpy as np

def check_nan(data):
    """Check for NaN in data"""
    return np.isnan(data).any()

def check_energy_conservation(energies):
    """Check energy conservation in NVE"""
    E0 = energies[0]
    drift = (energies[-1] - E0) / E0
    return abs(drift) < 0.01  # 1% threshold

def check_temperature_stability(temperatures):
    """Check temperature stability in NVT"""
    T_mean = np.mean(temperatures)
    T_std = np.std(temperatures)
    return T_std / T_mean < 0.01  # 1% fluctuation

def check_pressure_convergence(pressures):
    """Check pressure convergence in NPT"""
    P_mean = np.mean(pressures[-100:])  # Last 1000 steps
    target = pressures[0]  # Target pressure (usually 0 for NPT)
    deviation = abs(P_mean - target) / (abs(target) + 1e-10)
    return deviation < 0.05  # Within 5% of target

def validate_log(logfile):
    """Parse LAMMPS log file"""
    # Implementation would parse thermo output
    # Returns dict with arrays of thermo data
    pass

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 validate_fix.py logfile")
        sys.exit(1)
    
    logfile = sys.argv[1]
    data = validate_log(logfile)
    
    # Run checks
    if check_nan(data['energy']):
        print("FAIL: NaN detected in energy")
    elif check_energy_conservation(data['energy']):
        print("PASS: Energy conservation acceptable")
    else:
        print("FAIL: Energy conservation too poor")
```

### Bash Validation Script
```bash
#!/bin/bash
# Quick validation test

lammps -in input.lammps > log.txt 2>&1

# Check for errors
if grep -q "ERROR" log.txt; then
    echo "FAIL: LAMMPS error detected"
    cat log.txt | grep "ERROR"
    exit 1
fi

# Check for NaN
if grep -i "nan" log.txt; then
    echo "FAIL: NaN detected"
    exit 1
fi

# Check completion
if ! grep -q "Loop time of" log.txt; then
    echo "FAIL: Simulation did not complete"
    exit 1
fi

echo "PASS: Basic validation successful"
```

## Validation Report Template

### Fix Validation Report

```
Fix Applied: [description of what changed]
Error Type: [NaN / Energy drift / Lost atoms / etc.]
Date: [validation date]

Level 1 Results:
- Basic Initialization: PASS/FAIL
- Error Resolved: YES/NO
- New Warnings: [list any new warnings]

Level 2 Results:
- Energy Conservation (NVE): [drift %]
- Temperature Stability (NVT): [T std/T_mean]
- Pressure Convergence (NPT): [P deviation]
- Property Validation: [comparison to literature]

Level 3 Results:
- Extended Simulation: [duration, outcome]
- Property Agreement: [%, compared to literature]
- Multiple Conditions: [test variations, outcomes]
- Performance: [time, parallel efficiency]

Overall Assessment: [PASS / FAIL / USE WITH CAUTION]

Recommendation:
- [SAFE for production use]
- [Use with caution, monitor X]
- [Requires further testing]
- [Do not use, escalate required]
```

## Decision Criteria

### PASS Criteria (Safe for Production)
- All Level 1 tests pass
- Energy drift < 1% in NVE
- Temperature std < 1% of mean
- Pressure within 5% of target
- Properties within 5% of literature
- No new warnings introduced

### USE WITH CAUTION Criteria (Accept with Monitoring)
- Level 1 tests pass
- Energy drift 1-5% in NVE
- Temperature std 1-5% of mean
- Pressure within 10% of target
- Properties within 10% of literature
- Minor warnings that don't affect results

### FAIL Criteria (Do Not Use)
- Level 1 tests fail
- Energy drift >5% in NVE
- Temperature or pressure unstable
- Properties >20% from literature
- New critical warnings
- Fix introduces new problems

### ESCALATE Criteria (Human Expert Review Required)
- Validation cannot be completed
- Fix works but results are unexpected
- Unknown error pattern after fix
- Fix requires domain knowledge beyond scope
- Performance is unacceptable

## Pre-Production Checklist

Before using fixed simulation in production:

- [ ] Fix validated at all 3 levels
- [ ] Energy conservation verified
- [ ] Temperature/pressure stability confirmed
- [ ] Properties validated against literature
- [ ] Extended test (10-100 ps) successful
- [ ] No new warnings in log file
- [ ] Performance acceptable (wall time reasonable)
- [ ] Fix documented in input script comments
- [ ] Original error does not recur

## Regression Testing

### When to Run Regression Tests
- After any fix applied
- Before production simulation
- After LAMMPS version update
- After changing system size or composition

### Regression Test Suite
```bash
# Run full validation
./run_basic_validation.sh
./run_thermostat_test.sh
./run_barostat_test.sh

# Parse results
python3 analyze_validation.py
```

### Continuous Monitoring
- Log energy drift in every production run
- Alert if drift exceeds 1%
- Track temperature/pressure stability
- Monitor for lost atoms warnings
- Flag property deviations >10% from literature
