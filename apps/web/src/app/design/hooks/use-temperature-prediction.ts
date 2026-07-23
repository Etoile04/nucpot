/**
 * useTemperaturePrediction — computes 8 physical features from composition,
 * then calls the temperature prediction API.
 *
 * Unlike phase prediction (which has a backend convenience endpoint
 * accepting raw composition), temperature prediction requires the 8
 * `PredictionFeatures` computed client-side via `computeAllFeatures()`.
 *
 * NFM-1744: Wire predictTemperature into recommendation drawer
 */

import { useState, useCallback } from "react"
import {
  predictTemperature,
  type TempPredictResponse,
} from "@/lib/design-api"
import { computeAllFeatures } from "../lib/feature-engineering"

type PredictionState = "idle" | "loading" | "success" | "unavailable"

interface UseTemperaturePredictionReturn {
  /** Current prediction state */
  readonly state: PredictionState
  /** Temperature prediction response data */
  readonly temperature: TempPredictResponse | null
  /** Compute features from composition and trigger temperature prediction */
  readonly predictFromComposition: (composition: Readonly<Record<string, number>>) => Promise<void>
  /** Clear prediction state */
  readonly clear: () => void
}

export function useTemperaturePrediction(): UseTemperaturePredictionReturn {
  const [state, setState] = useState<PredictionState>("idle")
  const [temperature, setTemperature] = useState<TempPredictResponse | null>(null)

  const predictFromComposition = useCallback(
    async (composition: Readonly<Record<string, number>>) => {
      setState("loading")
      setTemperature(null)

      try {
        const features = computeAllFeatures(composition)
        const result = await predictTemperature(features)
        setTemperature(result)
        setState("success")
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Temperature prediction failed"
        // eslint-disable-next-line no-console -- intentional: prediction is non-critical
        console.warn(`[ML Temperature] Service unavailable: ${message}`)
        setState("unavailable")
        setTemperature(null)
      }
    },
    [],
  )

  const clear = useCallback(() => {
    setState("idle")
    setTemperature(null)
  }, [])

  return { state, temperature, predictFromComposition, clear }
}