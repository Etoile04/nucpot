/**
 * usePrediction — fetches ML phase prediction for a selected Pareto point.
 *
 * Supports two prediction modes:
 *   1. From 8 physical features (PhasePredictRequest)
 *   2. From raw alloy composition (CompositionPredictRequest) — preferred for
 *      Pareto point clicks since we only have composition from the optimizer.
 *
 * NFM-1698 §3
 */

import { useState, useCallback } from "react"
import {
  predictPhase,
  predictPhaseFromComposition,
  type PhasePredictResponse,
  type PhasePredictRequest,
} from "@/lib/design-api"

type PredictionState = "idle" | "loading" | "success" | "unavailable"

interface UsePredictionReturn {
  /** Current prediction state */
  readonly state: PredictionState
  /** Prediction response data */
  readonly prediction: PhasePredictResponse | null
  /** Trigger a prediction with 8 physical features */
  readonly predict: (features: PhasePredictRequest) => Promise<void>
  /** Trigger a prediction from raw composition (convenience endpoint) */
  readonly predictFromComposition: (composition: Readonly<Record<string, number>>) => Promise<void>
  /** Clear prediction state */
  readonly clear: () => void
}

export function usePrediction(): UsePredictionReturn {
  const [state, setState] = useState<PredictionState>("idle")
  const [prediction, setPrediction] = useState<PhasePredictResponse | null>(null)

  const predict = useCallback(async (features: PhasePredictRequest) => {
    setState("loading")
    setPrediction(null)

    try {
      const result = await predictPhase(features)
      setPrediction(result)
      setState("success")
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Prediction failed"
      // eslint-disable-next-line no-console -- intentional: prediction is non-critical
      console.warn(`[ML Prediction] Service unavailable: ${message}`)
      setState("unavailable")
      setPrediction(null)
    }
  }, [])

  const predictFromComposition = useCallback(
    async (composition: Readonly<Record<string, number>>) => {
      setState("loading")
      setPrediction(null)

      try {
        const result = await predictPhaseFromComposition({ composition })
        setPrediction(result)
        setState("success")
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Prediction failed"
        // eslint-disable-next-line no-console -- intentional: prediction is non-critical
        console.warn(`[ML Prediction] Service unavailable: ${message}`)
        setState("unavailable")
        setPrediction(null)
      }
    },
    [],
  )

  const clear = useCallback(() => {
    setState("idle")
    setPrediction(null)
  }, [])

  return { state, prediction, predict, predictFromComposition, clear }
}
