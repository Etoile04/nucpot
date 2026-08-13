/**
 * Mock API response data for Design Workspace E2E tests.
 *
 * Mirrors the Pydantic response schemas from:
 *   - apps/api/src/nfm_db/schemas/design.py  (optimize endpoint)
 *   - apps/api/src/nfm_db/schemas/prediction.py (predict endpoints)
 *
 * Usage:
 *   import { MOCK_OPTIMIZE_RESPONSE } from './design-workspace-mock-data'
 */

// =============================================================================
// API response envelope (matches apps/api/src/nfm_db/schemas/common.py)
// =============================================================================

export function wrapSuccess<T>(data: T): { success: boolean; data: T } {
  return { success: true, data }
}

// =============================================================================
// POST /api/v1/design/optimize — mock responses
// =============================================================================

const MOCK_PARETO_SOLUTIONS = [
  {
    composition: { U: 0.75, Mo: 0.10, Nb: 0.08, Zr: 0.04, Ti: 0.03 },
    objectives: {
      u_density: 19.05,
      phase_temp: 823.4,
      fabricability: 0.72,
    },
    rank: 1,
  },
  {
    composition: { U: 0.70, Mo: 0.15, Nb: 0.10, Zr: 0.03, Ti: 0.02 },
    objectives: {
      u_density: 18.72,
      phase_temp: 856.1,
      fabricability: 0.65,
    },
    rank: 1,
  },
  {
    composition: { U: 0.80, Mo: 0.08, Nb: 0.06, Zr: 0.04, V: 0.02 },
    objectives: {
      u_density: 19.42,
      phase_temp: 798.7,
      fabricability: 0.81,
    },
    rank: 1,
  },
  {
    composition: { U: 0.72, Mo: 0.12, Nb: 0.09, Zr: 0.04, Ta: 0.03 },
    objectives: {
      u_density: 18.90,
      phase_temp: 841.2,
      fabricability: 0.68,
    },
    rank: 1,
  },
  {
    composition: { U: 0.78, Mo: 0.09, Nb: 0.07, Zr: 0.04, Cr: 0.02 },
    objectives: {
      u_density: 19.18,
      phase_temp: 812.5,
      fabricability: 0.76,
    },
    rank: 1,
  },
]

export const MOCK_OPTIMIZE_RESPONSE = {
  pareto_front: MOCK_PARETO_SOLUTIONS,
  convergence: {
    gd_history: [0.45, 0.32, 0.21, 0.15, 0.11, 0.08, 0.06],
    hv_history: [0.12, 0.28, 0.41, 0.52, 0.59, 0.63, 0.65],
  },
  n_solutions: 5,
  compute_time_ms: 3420,
  algorithm_params: {
    pop_size: 200,
    n_gen: 100,
    seed: 42,
  },
  warnings: [],
}

export const MOCK_EMPTY_PARETO_RESPONSE = {
  pareto_front: [],
  convergence: { gd_history: [], hv_history: [] },
  n_solutions: 0,
  compute_time_ms: 1200,
  algorithm_params: { pop_size: 200, n_gen: 100, seed: 42 },
  warnings: ["Optimization produced no feasible Pareto-optimal solutions."],
}

// =============================================================================
// POST /api/v1/predict/phase — mock response
// =============================================================================

export const MOCK_PHASE_PREDICT_RESPONSE = {
  predicted_phase: "alpha+gamma two-phase",
  predicted_phase_label: "α+γ two-phase",
  probabilities: [
    { class_label: "I", probability: 0.05 },
    { class_label: "II", probability: 0.82 },
    { class_label: "single_phase", probability: 0.08 },
    { class_label: "multi_phase", probability: 0.05 },
  ],
  confidence: 0.82,
  warnings: [],
  model_version: "v1.1",
}

// =============================================================================
// POST /api/v1/predict/temperature — mock response
// =============================================================================

export const MOCK_TEMP_PREDICT_RESPONSE = {
  predicted_temp_c: 612.3,
  confidence_lower_c: 598.1,
  confidence_upper_c: 626.5,
  gpr_predicted_temp_c: 610.8,
  svr_predicted_temp_c: 613.7,
  confidence: 0.85,
  warnings: [],
  model_version: "v1.1-temp",
}

// =============================================================================
// Error responses
// =============================================================================

export const OPTIMIZE_ERROR_RESPONSE = {
  success: false,
  error: "Optimization failed: ML surrogate models are not available.",
}

export const VALIDATION_ERROR_RESPONSE = {
  success: false,
  error: "Validation error: u_min must not exceed u_max",
}
