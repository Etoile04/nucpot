/**
 * Frontend feature engineering — mirrors `compute_all_features()` from
 * `nfm_db.ml.feature_engineering` (Python).
 *
 * Computes the same 8 physical features from raw alloy composition
 * so the temperature prediction endpoint can be called without a
 * backend convenience route.
 *
 * NFM-1744: Temperature prediction wiring
 */

// ---------------------------------------------------------------------------
// Physical Constants
// ---------------------------------------------------------------------------

const GAS_CONSTANT_R = 8.314 // J/(mol·K)

// ---------------------------------------------------------------------------
// Feature 1: Mo Equivalent (Mo_eq)
// Formula: Mo_eq = 1.0×Mo + 1.13×Nb + 2.42×V + 1.86×Ti + 1.1×Zr
// ---------------------------------------------------------------------------

const MO_EQUIVALENT_COEFFICIENTS: Readonly<Record<string, number>> = {
  Mo: 1.0,
  Nb: 1.13,
  V: 2.42,
  Ti: 1.86,
  Zr: 1.1,
}

function calculateMoEquivalent(composition: Readonly<Record<string, number>>): number {
  return Object.entries(MO_EQUIVALENT_COEFFICIENTS).reduce(
    (sum, [element, coefficient]) => sum + (composition[element] ?? 0) * coefficient,
    0,
  )
}

// ---------------------------------------------------------------------------
// Feature 2: Pauling Electronegativity Difference (Δχ_p)
// ---------------------------------------------------------------------------

const PAULING_ELECTRONEGATIVITY: Readonly<Record<string, number>> = {
  H: 2.2, Li: 0.98, Be: 1.57, B: 2.04, C: 2.55,
  N: 3.04, O: 3.44, F: 3.98, Na: 0.93, Mg: 1.31,
  Al: 1.61, Si: 1.9, P: 2.19, S: 2.58, Cl: 3.16,
  K: 0.82, Ca: 1.0, Sc: 1.36, Ti: 1.54, V: 1.63,
  Cr: 1.66, Mn: 1.55, Fe: 1.83, Co: 1.88, Ni: 1.91,
  Cu: 1.9, Zn: 1.65, Ga: 1.81, Ge: 2.01, As: 2.18,
  Se: 2.55, Br: 2.96, Rb: 0.82, Sr: 0.95, Y: 1.22,
  Zr: 1.33, Nb: 1.6, Mo: 2.16, Tc: 1.9, Ru: 2.2,
  Rh: 2.28, Pd: 2.2, Ag: 1.93, Cd: 1.69, In: 1.78,
  Sn: 1.96, Sb: 2.05, Te: 2.1, I: 2.66, Cs: 0.79,
  Ba: 0.89, La: 1.1, Ce: 1.12, Pr: 1.13, Nd: 1.14,
  Sm: 1.17, Eu: 1.2, Gd: 1.2, Tb: 1.2, Dy: 1.22,
  Ho: 1.23, Er: 1.24, Tm: 1.25, Yb: 1.1, Lu: 1.27,
  Hf: 1.3, Ta: 1.5, W: 2.36, Re: 1.9, Os: 2.2,
  Ir: 2.2, Pt: 2.28, Au: 2.54, Hg: 2.0, Tl: 1.62,
  Pb: 2.33, Bi: 2.02, Th: 1.3, Pa: 1.5, U: 1.38,
  Np: 1.36, Pu: 1.28, Am: 1.3,
}

const CHI_U_PAULING = 1.38

function calculatePaulingChiDiff(composition: Readonly<Record<string, number>>): number {
  return Object.entries(composition).reduce(
    (sum, [element, fraction]) =>
      sum + fraction * Math.abs((PAULING_ELECTRONEGATIVITY[element] ?? CHI_U_PAULING) - CHI_U_PAULING),
    0,
  )
}

// ---------------------------------------------------------------------------
// Feature 3: Allen Electronegativity Difference (Δχ_a)
// ---------------------------------------------------------------------------

const ALLEN_ELECTRONEGATIVITY: Readonly<Record<string, number>> = {
  H: 2.3, Li: 0.912, Be: 1.576, B: 2.051, C: 2.544,
  N: 3.066, O: 3.61, F: 4.193, Na: 0.869, Mg: 1.293,
  Al: 1.613, Si: 1.916, P: 2.253, S: 2.589, Cl: 2.869,
  K: 0.734, Ca: 1.034, Sc: 1.264, Ti: 1.539, V: 1.652,
  Cr: 1.658, Mn: 1.747, Fe: 1.839, Co: 1.881, Ni: 1.899,
  Cu: 1.854, Zn: 1.59, Ga: 1.756, Ge: 1.994, As: 2.211,
  Se: 2.424, Br: 2.685, Rb: 0.706, Sr: 0.963, Y: 1.121,
  Zr: 1.399, Nb: 1.653, Mo: 1.885, Tc: 1.92, Ru: 2.058,
  Rh: 2.11, Pd: 2.1, Ag: 1.853, Cd: 1.672, In: 1.782,
  Sn: 1.925, Sb: 2.042, Te: 2.158, I: 2.359, Cs: 0.659,
  Ba: 0.881, La: 1.027, Ce: 1.06, Pr: 1.073, Nd: 1.083,
  Sm: 1.111, Eu: 1.12, Gd: 1.121, Tb: 1.134, Dy: 1.152,
  Ho: 1.165, Er: 1.18, Tm: 1.196, Yb: 1.067, Lu: 1.208,
  Hf: 1.323, Ta: 1.472, W: 1.835, Re: 1.89, Os: 1.977,
  Ir: 2.025, Pt: 2.128, Au: 2.254, Hg: 1.764, Tl: 1.644,
  Pb: 1.854, Bi: 1.91, Th: 1.138, Pa: 1.244, U: 1.226,
  Np: 1.209, Pu: 1.148, Am: 1.13,
}

const CHI_U_ALLEN = 1.226

function calculateAllenChiDiff(composition: Readonly<Record<string, number>>): number {
  return Object.entries(composition).reduce(
    (sum, [element, fraction]) =>
      sum + fraction * Math.abs((ALLEN_ELECTRONEGATIVITY[element] ?? CHI_U_ALLEN) - CHI_U_ALLEN),
    0,
  )
}

// ---------------------------------------------------------------------------
// Feature 4: Configuration Entropy (S_config)
// Formula: S_config = -R × Σ(x_i × ln(x_i))
// ---------------------------------------------------------------------------

function calculateConfigEntropy(composition: Readonly<Record<string, number>>): number {
  const total = Object.values(composition).reduce((s, v) => s + v, 0)
  if (total <= 0) return 0

  let entropy = 0
  for (const frac of Object.values(composition)) {
    const f = frac / total
    if (f > 0) {
      entropy += f * Math.log(f)
    }
  }
  return -GAS_CONSTANT_R * entropy
}

// ---------------------------------------------------------------------------
// Feature 5: Bulk Modulus / Volume Ratio (B/V)
// ---------------------------------------------------------------------------

const BULK_MODULUS: Readonly<Record<string, number>> = {
  U: 113, Mo: 263, Nb: 170, V: 162, Ti: 110, Zr: 94,
  Cr: 160, Fe: 170, Ni: 180, Ru: 220, Rh: 240, Pd: 180,
  Al: 76, Si: 98, Co: 200, Cu: 140, W: 311, Ta: 186,
  Hf: 110, Re: 370, Os: 395, Ir: 328, Pt: 230, Au: 180,
  Th: 54, Pa: 97, Np: 48, Pu: 40,
}

const ATOMIC_VOLUME_CM3_PER_MOL: Readonly<Record<string, number>> = {
  U: 12.49, Mo: 9.38, Nb: 10.83, V: 8.35, Ti: 10.63, Zr: 14.02,
  Cr: 7.23, Fe: 7.09, Ni: 6.59, Ru: 8.28, Rh: 8.28, Pd: 8.87,
  Al: 10.0, Si: 12.06, Co: 6.67, Cu: 7.11, W: 9.53, Ta: 10.85,
  Hf: 13.44, Re: 8.86, Os: 8.5, Ir: 8.54, Pt: 9.09, Au: 10.2,
  Th: 19.91, Pa: 15.0, Np: 12.3, Pu: 12.0,
}

function calculateBvRatio(composition: Readonly<Record<string, number>>): number {
  const total = Object.values(composition).reduce((s, v) => s + v, 0)
  if (total <= 0) return 0

  let weightedSum = 0
  let knownFraction = 0

  for (const [element, frac] of Object.entries(composition)) {
    const bm = BULK_MODULUS[element]
    const vol = ATOMIC_VOLUME_CM3_PER_MOL[element]
    if (bm !== undefined && vol !== undefined && vol > 0) {
      const normFrac = frac / total
      weightedSum += normFrac * (bm / vol)
      knownFraction += normFrac
    }
  }

  return knownFraction > 0 ? weightedSum / knownFraction : 0
}

// ---------------------------------------------------------------------------
// Feature 6: Theoretical Uranium Density (ρ_U)
// Formula: ρ = (Σ x_i × A_i) / (Σ x_i × V_i)
// ---------------------------------------------------------------------------

const ATOMIC_WEIGHT: Readonly<Record<string, number>> = {
  H: 1.008, He: 4.003, Li: 6.941, Be: 9.012, B: 10.811, C: 12.011,
  N: 14.007, O: 15.999, F: 18.998, Na: 22.99, Mg: 24.305, Al: 26.982,
  Si: 28.086, P: 30.974, S: 32.065, Cl: 35.453, K: 39.098, Ca: 40.078,
  Sc: 44.956, Ti: 47.867, V: 50.942, Cr: 51.996, Mn: 54.938, Fe: 55.845,
  Co: 58.933, Ni: 58.693, Cu: 63.546, Zn: 65.38, Ga: 69.723, Ge: 72.63,
  As: 74.922, Se: 78.971, Br: 79.904, Rb: 85.468, Sr: 87.62, Y: 88.906,
  Zr: 91.224, Nb: 92.906, Mo: 95.95, Tc: 98.0, Ru: 101.07, Rh: 102.91,
  Pd: 106.42, Ag: 107.87, Cd: 112.41, In: 114.82, Sn: 118.71, Sb: 121.76,
  Te: 127.6, I: 126.9, Cs: 132.91, Ba: 137.33, La: 138.91, Ce: 140.12,
  Pr: 140.91, Nd: 144.24, Sm: 150.36, Eu: 151.96, Gd: 157.25, Tb: 158.93,
  Dy: 162.5, Ho: 164.93, Er: 167.26, Tm: 168.93, Yb: 173.05, Lu: 174.97,
  Hf: 178.49, Ta: 180.95, W: 183.84, Re: 186.21, Os: 190.23, Ir: 192.22,
  Pt: 195.08, Au: 196.97, Hg: 200.59, Tl: 204.38, Pb: 207.2, Bi: 208.98,
  Th: 232.04, Pa: 231.04, U: 238.03, Np: 237.05, Pu: 244.06, Am: 243.06,
}

function calculateUDensity(composition: Readonly<Record<string, number>>): number {
  const total = Object.values(composition).reduce((s, v) => s + v, 0)
  if (total <= 0) return 0

  let massSum = 0
  let volSum = 0
  let knownFraction = 0

  for (const [element, frac] of Object.entries(composition)) {
    const aw = ATOMIC_WEIGHT[element]
    const vol = ATOMIC_VOLUME_CM3_PER_MOL[element]
    if (aw !== undefined && vol !== undefined) {
      const normFrac = frac / total
      massSum += normFrac * aw
      volSum += normFrac * vol
      knownFraction += normFrac
    }
  }

  return volSum > 0 && knownFraction > 0 ? massSum / volSum : 0
}

// ---------------------------------------------------------------------------
// Feature 7: Mixing Enthalpy (ΔH_mix) — Miedema Model
// Formula: ΔH_mix = Σ_{i<j} Ω_ij × x_i × x_j
// ---------------------------------------------------------------------------

type MiedemaKey = `${string}:${string}`

const MIEDEMA_PAIRS: Readonly<Record<MiedemaKey, number>> = {
  // U-X pairs
  "U:Mo": -5, "Mo:U": -5, "U:Nb": -4, "Nb:U": -4,
  "U:Ti": 18, "Ti:U": 18, "U:Zr": 6, "Zr:U": 6,
  "U:V": 20, "V:U": 20, "U:Cr": 10, "Cr:U": 10,
  "U:Fe": 15, "Fe:U": 15, "U:Ni": 25, "Ni:U": 25,
  "U:Ru": 30, "Ru:U": 30, "U:Rh": 45, "Rh:U": 45,
  "U:Pd": 55, "Pd:U": 55, "U:Al": 30, "Al:U": 30,
  "U:Si": 65, "Si:U": 65, "U:Co": 18, "Co:U": 18,
  "U:Cu": 22, "Cu:U": 22, "U:W": 0, "W:U": 0,
  "U:Ta": 10, "Ta:U": 10, "U:Hf": 4, "Hf:U": 4,
  "U:Re": 5, "Re:U": 5,
  // Mo-X pairs
  "Mo:Nb": 0, "Nb:Mo": 0, "Mo:Ti": -4, "Ti:Mo": -4,
  "Mo:Zr": 5, "Zr:Mo": 5, "Mo:V": 0, "V:Mo": 0,
  "Mo:Cr": -1, "Cr:Mo": -1, "Mo:Fe": -11, "Fe:Mo": -11,
  "Mo:Ni": -7, "Ni:Mo": -7, "Mo:Co": -5, "Co:Mo": -5,
  "Mo:W": 0, "W:Mo": 0, "Mo:Ta": -2, "Ta:Mo": -2,
  "Mo:Al": -1, "Al:Mo": -1, "Mo:Si": -12, "Si:Mo": -12,
  // Nb-X pairs
  "Nb:Ti": 2, "Ti:Nb": 2, "Nb:Zr": 0, "Zr:Nb": 0,
  "Nb:V": 0, "V:Nb": 0, "Nb:Cr": -7, "Cr:Nb": -7,
  "Nb:Fe": -13, "Fe:Nb": -13, "Nb:Ni": -17, "Ni:Nb": -17,
  "Nb:Co": -12, "Co:Nb": -12, "Nb:Al": -18, "Al:Nb": -18,
  "Nb:Si": -38, "Si:Nb": -38, "Nb:Ta": -1, "Ta:Nb": -1,
  // Ti-X pairs
  "Ti:Zr": 0, "Zr:Ti": 0, "Ti:V": -2, "V:Ti": -2,
  "Ti:Cr": -7, "Cr:Ti": -7, "Ti:Fe": -17, "Fe:Ti": -17,
  "Ti:Ni": -21, "Ni:Ti": -21, "Ti:Co": -14, "Co:Ti": -14,
  "Ti:Al": -30, "Al:Ti": -30, "Ti:Si": -37, "Si:Ti": -37,
  "Ti:Hf": 0, "Hf:Ti": 0, "Ti:Ta": 2, "Ta:Ti": 2,
  // Zr-X pairs
  "Zr:V": 4, "V:Zr": 4, "Zr:Cr": -1, "Cr:Zr": -1,
  "Zr:Fe": -12, "Fe:Zr": -12, "Zr:Ni": -24, "Ni:Zr": -24,
  "Zr:Co": -16, "Co:Zr": -16, "Zr:Al": -44, "Al:Zr": -44,
  "Zr:Si": -84, "Si:Zr": -84, "Zr:Hf": 0, "Hf:Zr": 0,
  "Zr:Ta": 3, "Ta:Zr": 3,
  // V-X pairs
  "V:Cr": -1, "Cr:V": -1, "V:Fe": -6, "Fe:V": -6,
  "V:Ni": -8, "Ni:V": -8, "V:Co": -6, "Co:V": -6,
  "V:Al": -16, "Al:V": -16, "V:Si": -36, "Si:V": -36,
  // Common TM pairs
  "Cr:Fe": -1, "Fe:Cr": -1, "Cr:Ni": -7, "Ni:Cr": -7,
  "Cr:Co": -4, "Co:Cr": -4, "Cr:Al": -10, "Al:Cr": -10,
  "Fe:Ni": -2, "Ni:Fe": -2, "Fe:Co": -1, "Co:Fe": -1,
  "Fe:Al": -11, "Al:Fe": -11, "Ni:Co": 0, "Co:Ni": 0,
  "Ni:Al": -22, "Al:Ni": -22, "Ni:Cu": 4, "Cu:Ni": 4,
  "Al:Si": -19, "Si:Al": -19, "W:Ta": 0, "Ta:W": 0,
  "Hf:Ta": 0, "Ta:Hf": 0,
}

function calculateMixingEnthalpy(composition: Readonly<Record<string, number>>): number {
  const total = Object.values(composition).reduce((s, v) => s + v, 0)
  if (total <= 0) return 0

  const fractions: Record<string, number> = {}
  for (const [el, frac] of Object.entries(composition)) {
    fractions[el] = frac / total
  }

  const elements = Object.keys(fractions)
  let deltaH = 0

  for (let i = 0; i < elements.length; i++) {
    for (let j = i + 1; j < elements.length; j++) {
      const omega = MIEDEMA_PAIRS[`${elements[i]}:${elements[j]}` as MiedemaKey]
      if (omega !== undefined) {
        deltaH += omega * fractions[elements[i]] * fractions[elements[j]]
      }
    }
  }

  return deltaH
}

// ---------------------------------------------------------------------------
// Feature 8: Lattice Distortion (δ)
// Formula: δ = √[Σ x_i × (1 − r_i/r̄)²]
// ---------------------------------------------------------------------------

const ATOMIC_RADIUS: Readonly<Record<string, number>> = {
  U: 1.56, Mo: 1.39, Nb: 1.43, V: 1.34, Ti: 1.47, Zr: 1.6,
  Cr: 1.28, Fe: 1.26, Ni: 1.24, Ru: 1.34, Rh: 1.34, Pd: 1.37,
  Al: 1.43, Si: 1.17, Co: 1.25, Cu: 1.28, W: 1.39, Ta: 1.43,
  Hf: 1.56, Re: 1.37, Os: 1.35, Ir: 1.36, Pt: 1.39, Au: 1.44,
  Th: 1.8, Pa: 1.61, Np: 1.56, Pu: 1.59, H: 0.53, B: 0.87,
  C: 0.77, N: 0.75, O: 0.73, Mn: 1.27, Zn: 1.33, Ga: 1.35,
  Ge: 1.39, As: 1.25, Sn: 1.45, Sb: 1.45, La: 1.87, Ce: 1.82,
  Nd: 1.82, Gd: 1.8, Dy: 1.77, Er: 1.76, Yb: 1.94,
}

function calculateLatticeDistortion(composition: Readonly<Record<string, number>>): number {
  const total = Object.values(composition).reduce((s, v) => s + v, 0)
  if (total <= 0) return 0

  let rAvg = 0
  let knownFraction = 0

  for (const [element, frac] of Object.entries(composition)) {
    const r = ATOMIC_RADIUS[element]
    if (r !== undefined) {
      const normFrac = frac / total
      rAvg += normFrac * r
      knownFraction += normFrac
    }
  }

  if (rAvg <= 0 || knownFraction <= 0) return 0

  let deltaSq = 0
  for (const [element, frac] of Object.entries(composition)) {
    const r = ATOMIC_RADIUS[element]
    if (r !== undefined) {
      const normFrac = frac / total
      deltaSq += normFrac * (1 - r / rAvg) ** 2
    }
  }

  return Math.sqrt(Math.max(deltaSq, 0))
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Output type matching the backend `PredictionFeatures` Pydantic schema. */
export interface ComputedFeatures {
  readonly mo_equivalent: number
  readonly pauling_chi_diff: number
  readonly allen_chi_diff: number
  readonly config_entropy: number
  readonly bv_ratio: number
  readonly u_density: number
  readonly mixing_enthalpy: number
  readonly lattice_distortion: number
}

/**
 * Compute all 8 physical features from raw alloy composition.
 *
 * Mirrors `compute_all_features()` from the Python backend.
 * Input composition is not mutated.
 *
 * @param composition - Element name to atomic fraction mapping.
 *   Values can be at.% (sum=100) or atomic fraction (sum=1).
 */
export function computeAllFeatures(
  composition: Readonly<Record<string, number>>,
): ComputedFeatures {
  return {
    mo_equivalent: calculateMoEquivalent(composition),
    pauling_chi_diff: calculatePaulingChiDiff(composition),
    allen_chi_diff: calculateAllenChiDiff(composition),
    config_entropy: calculateConfigEntropy(composition),
    bv_ratio: calculateBvRatio(composition),
    u_density: calculateUDensity(composition),
    mixing_enthalpy: calculateMixingEnthalpy(composition),
    lattice_distortion: calculateLatticeDistortion(composition),
  }
}