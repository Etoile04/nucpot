/**
 * API client for design optimization and ML prediction endpoints.
 *
 * POST /api/v1/design/optimize — NSGA-II multi-objective optimization
 * POST /api/v1/predict/phase — ML phase classification
 *
 * NFM-1698
 */

import { request, type ApiResponse } from "./api-client"

// ---------------------------------------------------------------------------
// Request types (mirror backend Pydantic schemas)
// ---------------------------------------------------------------------------

export interface ObjectiveWeightsRequest {
  readonly u_density: number
  readonly phase_temp: number
  readonly fabricability: number
}

export interface OptimizationConstraintsRequest {
  readonly u_min: number
  readonly u_max: number
  readonly max_single_element: number
  readonly n_elements: readonly [number, number]
  readonly bv_ratio: readonly [number, number]
}

export interface AlgorithmParamsRequest {
  readonly pop_size: number
  readonly n_gen: number
  readonly seed: number | null
}

export interface OptimizeRequest {
  readonly objectives: ObjectiveWeightsRequest
  readonly constraints?: OptimizationConstraintsRequest
  readonly algorithm: AlgorithmParamsRequest
}

// ---------------------------------------------------------------------------
// Response types (mirror backend Pydantic schemas)
// ---------------------------------------------------------------------------

export interface ParetoSolutionResponse {
  readonly composition: Readonly<Record<string, number>>
  readonly objectives: Readonly<Record<string, number>>
  readonly rank: number
}

export interface ConvergenceMetricsResponse {
  readonly gd_history: readonly number[]
  readonly hv_history: readonly number[]
}

export interface OptimizeResponse {
  readonly pareto_front: readonly ParetoSolutionResponse[]
  readonly convergence: ConvergenceMetricsResponse
  readonly n_solutions: number
  readonly compute_time_ms: number
  readonly algorithm_params: AlgorithmParamsRequest
  readonly warnings: readonly string[]
}

// ---------------------------------------------------------------------------
// Prediction request/response types
// ---------------------------------------------------------------------------

export interface PhasePredictRequest {
  readonly mo_equivalent: number
  readonly pauling_chi_diff: number
  readonly allen_chi_diff: number
  readonly config_entropy: number
  readonly bv_ratio: number
  readonly u_density: number
  readonly mixing_enthalpy: number
  readonly lattice_distortion: number
}

export interface PhaseProbabilityItem {
  readonly class_label: string
  readonly probability: number
}

export interface PredictionWarningItem {
  readonly code: string
  readonly message: string
}

export interface PhasePredictResponse {
  readonly predicted_phase: string
  readonly predicted_phase_label: string
  readonly probabilities: readonly PhaseProbabilityItem[]
  readonly confidence: number
  readonly warnings: readonly PredictionWarningItem[]
  readonly model_version: string
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/** POST /api/v1/design/optimize — run NSGA-II optimization */
export async function runOptimization(
  payload: OptimizeRequest,
): Promise<OptimizeResponse> {
  const envelope = await request<ApiResponse<OptimizeResponse>>(
    "/api/v1/design/optimize",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  )
  return envelope.data
}

/** POST /api/v1/predict/phase — ML phase classification */
export async function predictPhase(
  features: PhasePredictRequest,
): Promise<PhasePredictResponse> {
  const envelope = await request<ApiResponse<PhasePredictResponse>>(
    "/api/v1/predict/phase",
    {
      method: "POST",
      body: JSON.stringify(features),
    },
  )
  return envelope.data
}
