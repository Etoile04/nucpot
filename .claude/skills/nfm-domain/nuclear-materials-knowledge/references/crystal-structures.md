# Crystal Structures for Nuclear Materials

## Uranium (U)

### α-Uranium (Alpha Phase)
- **Temperature Range**: < 942 K (669 °C)
- **Crystal System**: Orthorhombic
- **Space Group**: Cmcm (No. 63)
- **Lattice Parameters**:
  - a = 2.854 Å
  - b = 5.869 Å  
  - c = 4.955 Å
- **Atomic Positions** (Wyckoff 4c):
  - U: (0, y, 1/4) with y ≈ 0.102
- **Key Properties**:
  - Anisotropic thermal expansion
  - Highly anisotropic mechanical properties
  - Orthorhombic distortion important for texture

### β-Uranium (Beta Phase)
- **Temperature Range**: 942 K - 1048 K (669 °C - 775 °C)
- **Crystal System**: Tetragonal
- **Space Group**: P4₂/mnm (No. 136)
- **Lattice Parameters**:
  - a = b = 10.759 Å
  - c = 5.656 Å
- **Notes**: Complex structure with 30 atoms per unit cell
- **Relevance**: Limited stability range, less commonly modeled

### γ-Uranium (Gamma Phase)
- **Temperature Range**: > 1048 K (> 775 °C)
- **Crystal System**: Body-Centered Cubic (BCC)
- **Space Group**: Im-3m (No. 229)
- **Lattice Parameter**: a = 3.525 Å (at melting)
- **Atomic Positions**: U at (0, 0, 0) and (1/2, 1/2, 1/2)
- **Key Properties**:
  - Isotropic cubic structure
  - High temperature phase relevant for accident scenarios
  - Most symmetric phase

---

## Uranium Dioxide (UO₂)

### Fluorite Structure
- **Temperature Range**: Stable up to melting (3138 K)
- **Crystal System**: Face-Centered Cubic (FCC)
- **Space Group**: Fm-3m (No. 225)
- **Lattice Parameter**: a = 5.470 Å (room temperature, stoichiometric)
- **Thermal Expansion**: a(T) = a₀[1 + α(T-T₀)] with α ≈ 10×10⁻⁶ K⁻¹
- **Atomic Positions**:
  - U: (0, 0, 0) [4a Wyckoff]
  - O: (1/4, 1/4, 1/4) [8c Wyckoff]
- **Stoichiometry Effects**:
  - UO₂±x affects lattice parameter
  - Hyperstoichiometric (UO₂+x): lattice expands
  - Hypostoichiometric (UO₂-x): lattice contracts
- **Key Properties**:
  - Fluorite structure: FCC cation sublattice with O in tetrahedral sites
  - O-O distance: 2.73 Å (edge of O cube)
  - U-O distance: 2.37 Å
  - Important: Oxygen vacancies dominate defect chemistry

---

## Zirconium (Zr)

### α-Zirconium (Alpha Phase)
- **Temperature Range**: < 1135 K (862 °C)
- **Crystal System**: Hexagonal Close-Packed (HCP)
- **Space Group**: P6₃/mmc (No. 194)
- **Lattice Parameters**:
  - a = 3.232 Å
  - c = 5.147 Å
  - c/a ratio = 1.593 (ideal HCP: 1.633)
- **Atomic Positions**:
  - Zr: (1/3, 2/3, 1/4) [2c Wyckoff]
- **Key Properties**:
  - Anisotropic thermal expansion (α_a ≈ 5.7×10⁻⁶ K⁻¹, α_c ≈ 10.6×10⁻⁶ K⁻¹)
  - Important for cladding applications
  - Preferred orientation common in fabricated components

### β-Zirconium (Beta Phase)
- **Temperature Range**: > 1135 K (> 862 °C)
- **Crystal System**: Body-Centered Cubic (BCC)
- **Space Group**: Im-3m (No. 229)
- **Lattice Parameter**: a = 3.609 Å (at transition)
- **Atomic Positions**:
  - Zr: (0, 0, 0) and (1/2, 1/2, 1/2)
- **Key Properties**:
  - Stable at reactor operating temperatures (with alloys)
  - Alloying elements (Sn, Fe, Cr, Nb) stabilize α phase
  - Important: Phase transition affects mechanical properties

---

## Iron (Fe)

### α-Iron (Ferrite, BCC Phase)
- **Temperature Range**: < 912 °C (1185 K) - stable at room temperature
- **Crystal System**: Body-Centered Cubic (BCC)
- **Space Group**: Im-3m (No. 229)
- **Lattice Parameter**: a = 2.866 Å (room temperature)
- **Atomic Positions**:
  - Fe: (0, 0, 0) and (1/2, 1/2, 1/2)
- **Magnetic**: Ferromagnetic below 770 °C (Curie temperature)

### γ-Iron (Austenite, FCC Phase)
- **Temperature Range**: 912 °C - 1394 °C
- **Crystal System**: Face-Centered Cubic (FCC)
- **Space Group**: Fm-3m (No. 225)
- **Lattice Parameter**: a = 3.656 Å (at 915 °C)
- **Atomic Positions**:
  - Fe: (0, 0, 0) and face centers

### δ-Iron (Delta Ferrite, BCC Phase)
- **Temperature Range**: 1394 °C - 1538 °C (melting)
- **Crystal System**: Body-Centered Cubic (BCC)
- **Lattice Parameter**: a = 2.93 Å (near melting)

---

## U-Zr Alloys

### β Phase (BCC Solid Solution)
- **Temperature Range**: > ~900 K (depends on Zr concentration)
- **Crystal System**: Body-Centered Cubic (BCC)
- **Space Group**: Im-3m (No. 229)
- **Lattice Parameter**: 
  - Pure U (γ): 3.525 Å
  - Pure Zr (β): 3.609 Å
  - U-xZr: Linear interpolation (Vegard's law): a(x) = a_U·(1-x) + a_Zr·x
- **Composition Range**: 0-100% Zr (complete solid solution in β phase)
- **Key Properties**:
  - Metallic bonding with miscibility gap at low T
  - Important for metallic fuel (U-10%Zr common)
  - Density: calculated from lattice parameter and composition
- **Simulation Considerations**:
  - Requires U-Zr cross potential for EAM
  - Phase separation possible at low temperatures
  - Thermal expansion varies with composition

---

## LAMMPS Implementation Notes

### Lattice Commands
```
# Example: γ-U (BCC)
lattice bcc 3.525
region box block 0 10 0 10 0 10
create_box 1 box
create_atoms 1 box

# Example: α-Zr (HCP)
lattice hcp 3.232 5.147
region box block 0 10 0 10 0 10
create_box 1 box
create_atoms 1 box

# Example: UO₂ (Fluorite, FCC with basis)
lattice fcc 5.470
region box block 0 5 0 5 0 5
create_box 2 box
create_atoms 1 box
create_atoms 2 box basis 2 2 2
```

### Temperature Considerations
- Always use lattice parameters for target temperature
- Apply thermal expansion: a(T) = a₀[1 + α·ΔT]
- For phase transitions: verify stable phase at simulation T
- Use `fix npt` for allowing box relaxation at target T

### Common Pitfalls
- Wrong phase for temperature (e.g., α-U above 942 K)
- Missing basis atoms (e.g., UO₂ oxygen sublattice)
- Incorrect lattice parameters causing high initial energy
- Ignoring stoichiometry effects on lattice parameters
