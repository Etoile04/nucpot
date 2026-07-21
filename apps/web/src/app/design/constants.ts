/**
 * Constants for the Composition Design Workbench.
 *
 * NFM-1668 §8 + NFM-1673 §10 + NFM-1696 (element bounds, optimization defaults)
 */

import type { ObjectiveKey, ConfigType, ObjectiveMeta, AxisPair } from "./types"

// =============================================================================
// §1  Objective & config type labels (UI display)
// =============================================================================

/** Objective display labels and descriptions (left panel) */
export const OBJECTIVES: Record<ObjectiveKey, {
  label: string
  description: string
}> = {
  u_density: {
    label: "铀密度 / U Density",
    description: "最大化铀密度 (g/cm³)",
  },
  phase_stability: {
    label: "相稳定性温度 / Phase Stability",
    description: "最大化相稳定性温度 (K)",
  },
  fabricability: {
    label: "可制备性 / Fabricability",
    description: "最大化可制备性评分 (0-1)",
  },
}

/** Configuration type display labels and tag colors (left panel) */
export const CONFIG_TYPES: Record<ConfigType, { label: string; color: string }> = {
  type_i: { label: "Type I", color: "blue" },
  type_ii: { label: "Type II", color: "green" },
  type_iii: { label: "Type III", color: "orange" },
  type_iv: { label: "Type IV", color: "purple" },
}

/** Objective metadata for chart axis labels and tooltips */
export const OBJECTIVE_META: Record<ObjectiveKey, ObjectiveMeta> = {
  u_density: {
    zh: "铀密度",
    en: "U Density",
    unit: "g/cm³",
  },
  phase_stability: {
    zh: "相稳定性温度",
    en: "Phase Stability",
    unit: "K",
  },
  fabricability: {
    zh: "可制备性",
    en: "Fabricability",
    unit: "",
  },
}

/** Config type display labels (for chart legend) */
export const CONFIG_TYPE_LABELS: Record<ConfigType, string> = {
  type_i: "Type I",
  type_ii: "Type II",
  type_iii: "Type III",
  type_iv: "Type IV",
}

/** Default axis pair (most scientifically meaningful) */
export const DEFAULT_AXIS_PAIR: AxisPair = {
  x: "u_density",
  y: "phase_stability",
}

/** All objective keys */
export const ALL_OBJECTIVES: ObjectiveKey[] = [
  "u_density",
  "phase_stability",
  "fabricability",
]

/** All config type keys */
export const ALL_CONFIG_TYPES: ConfigType[] = [
  "type_i",
  "type_ii",
  "type_iii",
  "type_iv",
]

/** Config type chart colors — matches DARK_PALETTE.category */
export const CONFIG_TYPE_CHART_COLORS: Record<ConfigType, string> = {
  type_i: "#60a5fa",
  type_ii: "#34d399",
  type_iii: "#fbbf24",
  type_iv: "#f87171",
}

// =============================================================================
// §2  Alloy element search space (mirrors ALLOY_ELEMENTS from nsga2_problem.py)
// =============================================================================

/** Candidate solute element with atomic fraction bounds. */
interface ElementBounds {
  readonly name: string
  readonly min: number
  readonly max: number
  readonly label: string
}

/**
 * Solute elements in the NSGA-II search space.
 * Values match ALLOY_ELEMENTS in apps/api/src/nfm_db/optimization/nsga2_problem.py
 * Units: atomic fraction (at%).
 */
export const ELEMENTS: readonly ElementBounds[] = [
  { name: "Mo", min: 0.5, max: 20.0, label: "Mo / 钼" },
  { name: "Nb", min: 0.5, max: 20.0, label: "Nb / 铌" },
  { name: "V",  min: 0.5, max: 20.0, label: "V  / 钒" },
  { name: "Ti", min: 0.5, max: 20.0, label: "Ti / 钛" },
  { name: "Zr", min: 0.5, max: 20.0, label: "Zr / 锆" },
  { name: "Cr", min: 0.5, max: 20.0, label: "Cr / 铬" },
] as const

/** Uranium content bounds (at%). */
export const U_BOUNDS = {
  min: 60,
  max: 90,
} as const

/** B/V ratio bounds. */
export const BV_RATIO_BOUNDS = {
  min: 8.0,
  max: 18.0,
} as const

/** Per-element ceiling (at%). */
export const MAX_SINGLE_ELEMENT = 20

/** Minimum/maximum active solute elements. */
export const ELEMENT_COUNT_RANGE = {
  min: 2,
  max: 6,
} as const

// =============================================================================
// §3  Default optimization parameters (mirrors OptimizeRequest defaults)
// =============================================================================

/** Default objective weights (match backend ObjectiveWeights defaults). */
export const DEFAULT_OBJECTIVES = {
  u_density: 1.0,
  phase_temp: 0.8,
  fabricability: 0.6,
} as const

/** Default constraints (match backend OptimizationConstraints defaults). */
export const DEFAULT_CONSTRAINTS = {
  u_min: 60,
  u_max: 90,
  max_single_element: 20,
  n_elements: [2, 6] as const,
  bv_ratio: [3.0, 6.5] as const,
} as const

/** Default algorithm parameters (match backend AlgorithmParams defaults). */
export const DEFAULT_ALGORITHM = {
  pop_size: 200,
  n_gen: 100,
  seed: 42,
} as const

// =============================================================================
// §4  Cluster type phase labels (mirrors CLUSTER_PHASE_LABELS)
// =============================================================================

/** Phase labels for each cluster type (Chinese + English descriptions). */
export const CLUSTER_PHASE_LABELS: Record<string, { zh: string; en: string }> = {
  I:   { zh: "α-U 单相",     en: "α-U (single phase)" },
  II:  { zh: "α+γ 两相",     en: "α+γ two-phase" },
  III: { zh: "γ 单相",       en: "γ (single phase)" },
  IV:  { zh: "非晶/亚稳态",   en: "amorphous / metastable" },
}
