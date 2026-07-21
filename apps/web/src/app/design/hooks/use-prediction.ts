/**
 * usePrediction — fetches ML phase prediction for a selected Pareto point.
 *
 * When a Pareto solution is selected, this hook attempts to predict
 * the phase classification. Since the predict endpoint requires 8 physical
 * features (not raw composition), we display prediction data when available
 * and show "unavailable" when the composition-to-features pipeline isn't
 * connected.
 *
 * NFM-1698 §3
 */

import { useState, useCallback } from "react"
import {
  predictPhase,
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
    } catch {
      // If the prediction service is unavailable (503), show unavailable
      // instead of a hard error — the optimization results are still valid.
      setState("unavailable")
      setPrediction(null)
    }
  }, [])

  const clear = useCallback(() => {
    setState("idle")
    setPrediction(null)
  }, [])

  return { state, prediction, predict, clear }
}
