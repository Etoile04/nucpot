# Non-EAM LAMMPS Templates

## Buckingham + Coulombic (Oxides)

### UO2 Template

```lammps
# UO2 Buckingham + Coulombic with PPPM

units           real
atom_style      charge
boundary        p p p

# Lattice definition
lattice         fcc 5.47
region          box block 0 10 0 10 0 10
create_box      2 box
create_atoms    1 box basis 1 1 1 1 0 0 &
                basis 2 2 2 0 0 0 &
                basis 3 3 3 0 0 0 &
                basis 4 4 4 0 0 0

# Assign charges (U=+4, O=-2)
set             type 1 charge 4.0
set             type 2 charge -2.0
mass            1 238.03
mass            2 16.00

# Potential: Buckingham + Coulombic
pair_style      buck/coul/long 12.0
pair_coeff      * * 0.0 1.0 0.0
# U-U interactions (example values - replace with actual parameters)
pair_coeff      1 1 1000.0 0.3 0.0
# O-O interactions
pair_coeff      2 2 1200.0 0.35 0.0
# U-O interactions (critical - well-parameterized)
pair_coeff      1 2 1500.0 0.32 50.0

# Long-range electrostatics with PPPM
kspace_style    pppm 1.0e-4
kspace_modify   mesh 8 8 8

# Neighbor settings
neighbor        2.0 bin
neigh_modify     every 1 delay 0 check yes

# Minimization
fix             1 all box/relax aniso 0.0 0.0 1000
thermo_style    custom step pe ke etotal press temp
thermo          100
minimize        1.0e-4 1.0e-6 100 1000
unfix           1

# Equilibration in NPT
velocity        all create 300.0 12345
fix             2 all npt temp 300 300 100 iso 0 0 1000
timestep        1.0
run             10000

# Production run
reset_timestep  0
fix             3 all nvt temp 300 300 100
compute         myTemp all temp
thermo_style    custom step temp pe ke etotal press c_myTemp
thermo          100
run             50000

# Output
dump            1 all custom 1000 dump.lammpstrj id type x y z
dump_modify     1 sort id
```

### Critical Notes for Buckingham

1. **Timestep**: 0.5 - 1.0 fs (stiffer than EAM)
2. **Cutoff**: 10-12 Å (long-range required)
3. **PPPM Accuracy**: 1.0e-4 to 1.0e-6 (controls electrostatic accuracy)
4. **Mesh Convergence**: Test 6x6x6 to 12x12x12
5. **Parameter Source**: See literature-search for validated parameters

## Tersoff Potential (Carbides, Covalent)

### UC Template

```lammps
# UC Tersoff potential

units           metal
atom_style      atomic
boundary        p p p

# NaCl structure for UC
lattice         fcc 4.96
region          box block 0 5 0 5 0 5
create_box      2 box
create_atoms    1 box basis 1 1 1 1 0 0 &
                basis 2 2 2 0 0 0 &
                basis 3 3 3 0 0 0 &
                basis 4 4 4 0 0 0

mass            1 238.03
mass            2 12.01

# Tersoff potential (example - replace with actual parameters)
pair_style      tersoff
pair_coeff      * * UC.tersoff U C

# Neighbor settings
neighbor        2.5 bin
neigh_modify     every 1 delay 0 check yes

# Minimization
minimize        1.0e-4 1.0e-6 1000 10000

# Equilibration
velocity        all create 300.0 54321
fix             1 all npt temp 300 300 100 iso 0 0 1000
timestep        0.5
run             10000

# Production
fix             2 all nvt temp 300 300 100
run             50000
```

### Critical Notes for Tersoff

1. **Timestep**: 0.25 - 0.5 fs (stiff covalent bonds)
2. **Cutoff**: 3.0x bond length (typically 6-8 Å)
3. **Parameter File**: Must be supplied separately
4. **Three-Body Terms**: Expensive but critical for covalent systems
5. **Validation**: Check bond angles and lengths against experiment

## AIREBO Potential (Organics, Mixed Systems)

### General Template

```lammps
# AIREBO potential for organic-inorganic interfaces

units           real
atom_style      molecular
boundary        p p p

# Create system
read_data       system.data

pair_style      airebo 3.0 1 1
pair_coeff      * * CH.airebo C H

# AIREBO parameters
# 3.0 = cutoff for REBO potential
# 1 1 = enable LJ and torsional terms

neighbor        2.0 bin
neigh_modify     every 1 delay 0 check yes

# Minimization
minimize        1.0e-4 1.0e-6 1000 10000

# Dynamics
velocity        all create 300.0 98765
fix             1 all npt temp 300 300 100 iso 0 0 1000
timestep        0.5
run             20000
```

### Critical Notes for AIREBO

1. **Timestep**: 0.5 - 1.0 fs
2. **REBO Cutoff**: ~2.0 Å (short-range)
3. **LJ Cutoff**: ~10 Å (long-range)
4. **Bond Order**: Critical for chemistry
5. **Reactions**: Can handle bond breaking/formation

## Hybrid Potentials (Mixed Bonding)

### EAM + Buckingham Template

```lammps
# Hybrid EAM + Buckingham for U-Zr-O system

units           metal
atom_style      atomic
boundary        p p p

read_data       uzro.data

# EAM for U-U, Zr-Zr, U-Zr
pair_style      hybrid/overlay eam/alloy buck/coul/long
pair_coeff      * * eam.alloy U Zr U_Zr.eam.alloy
pair_coeff      1 3 buck/coul/long 1000.0 0.3 0.0
pair_coeff      2 3 buck/coul/long 1200.0 0.35 0.0

# Charges for O (if present)
set             type 3 charge -2.0
kspace_style    pppm 1.0e-4

# ... rest of template similar to Buckingham examples
```

### Critical Notes for Hybrid

1. **Complexity**: High parameter count
2. **Validation**: Essential for each pair interaction
3. **Computational Cost**: Higher than single potentials
4. **Convergence**: More difficult to achieve

## Template Customization

### Stage Selection

Always include three stages:
1. **Minimization**: Energy minimization to remove bad contacts
2. **Equilibration**: NPT or NVT to reach target T, P
3. **Production**: Data collection run

### Output Customization

```lammps
# Thermodynamics
compute         myPE all pe
compute         myKE all ke
compute         myStress all pressure thermo/temp

# Trajectory
dump            1 all custom 1000 traj.lammpstrj id type x y z
dump_modify     1 sort id

# Properties
compute         rdf all rdf 100
fix             outrdf all ave/time 100 100 10000 c_rdf file rdf.txt
```

## Validation Checklist

After running template:
- [ ] Energy conservation in NVE (drift < 0.01 kcal/mol/ps)
- [ ] Reasonable structure (bond lengths, angles)
- [ ] Stable temperature and pressure
- [ ] Converged thermodynamics (no drift in production)
- [ ] Appropriate diffusion (if simulating transport)

## Cross-References

- See property-potential-mapping.md for selection logic
- See parameter-estimation.md for convergence settings
- See nuclear-materials-knowledge for material parameters
