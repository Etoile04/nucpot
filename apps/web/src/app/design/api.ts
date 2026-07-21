/**
 * Typed API client for the Composition Design Workbench.
 *
 * NFM-1696: Design workspace foundation — API client
 *
 * Uses the shared `request<T>` wrapper from lib/api-client.ts.
 * All endpoints return the standard `ApiResponse<T>` envelope.
 */

import { request, type ApiResponse } from "@/lib/api-client"
import type {
  OptimizeRequest,
  OptimizeResponse,
  PhasePredictRequest,
  PhasePredictResponse,
  TempPredictRequest,
  TempPredictResponse,
} from "./types"

// =============================================================================
// Design optimization API
// =============================================================================

/**
 * Start an NSGA-II multi-objective optimization run.
 *
 * POST /api/v1/design/optimize
 *
 * @param params - Optimization objectives, constraints, and algorithm parameters.
 * @returns The Pareto-optimal solutions with convergence metrics.
 */
export async function startOptimization(
  params: OptimizeRequest,
): Promise<OptimizeResponse> {
  const envelope = await request<ApiResponse<OptimizeResponse>>(
    "/api/v1/design/optimize",
    {
      method: "POST",
      body: JSON.stringify(params),
    },
  )
  return envelope.data
}

// =============================================================================
// ML prediction API
// =============================================================================

/**
 * Predict phase type from 8 physical features.
 *
 * POST /api/v1/predict/phase
 *
 * @param features - 8 physical features computed from alloy composition.
 * @returns Predicted phase type with probabilities and confidence score.
 */
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

/**
 * Predict phase transition temperature from 8 physical features.
 *
 * POST /api/v1/predict/temperature
 *
 * @param features - 8 physical features computed from alloy composition.
 * @returns Predicted temperature with confidence interval and model breakdown.
 */
export async function predictTemperature(
  features: TempPredictRequest,
): Promise<TempPredictResponse> {
  const envelope = await request<ApiResponse<TempPredictResponse>>(
    "/api/v1/predict/temperature",
    {
      method: "POST",
      body: JSON.stringify(features),
    },
  )
  return envelope.data
}
