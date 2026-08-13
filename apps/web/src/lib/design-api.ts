/**
 * Re-export shim — design workspace API client.
 *
 * All types and functions are canonical in app/design/api.ts and
 * app/design/types.ts. This module re-exports them so existing
 * imports from "@/lib/design-api" continue to work without changes.
 *
 * NFM-1700: Removed duplicate type definitions (review reject fix).
 */

export {
  startOptimization as runOptimization,
  predictPhase,
  predictPhaseFromComposition,
  predictTemperature,
} from "@/app/design/api"

export type {
  OptimizeRequest,
  OptimizeResponse,
  PhasePredictRequest,
  PhasePredictResponse,
  CompositionPredictRequest,
  PredictionWarningItem,
  TempPredictRequest,
  TempPredictResponse,
} from "@/app/design/types"
