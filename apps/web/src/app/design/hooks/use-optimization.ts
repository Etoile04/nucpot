/**
 * useOptimization — manages the full optimization lifecycle with 4-state handling.
 *
 * States: idle → loading → success | error
 *
 * NFM-1698
 */

import { useState, useCallback, useRef } from "react"
import type { ParetoSolution, ConvergenceData, ConfigType } from "../types"
import {
  runOptimization,
  type OptimizeResponse,
  type OptimizeRequest,
} from "@/lib/design-api"

type OptimizationState = "idle" | "loading" | "success" | "error"

interface UseOptimizationReturn {
  /** Current lifecycle state */
  readonly state: OptimizationState
  /** Pareto front data (populated on success) */
  readonly paretoData: ParetoSolution[]
  /** Convergence history (populated on success) */
  readonly convergenceData: ConvergenceData
  /** Error message (populated on error) */
  readonly error: string | null
  /** Simulated progress 0-100 during loading */
  readonly progress: number
  /** Current generation estimate during loading */
  readonly generation: number
  /** Total generations requested */
  readonly totalGenerations: number
  /** Start the optimization run */
  readonly startOptimization: (params: OptimizeRequest) => Promise<void>
  /** Reset back to idle */
  readonly reset: () => void
}

/**
 * Derive config type from the number of solute elements in the composition.
 *
 * Matches CLUSTER_PHASE_LABELS convention:
 *   ≤2 elements → Type I (α-U single phase)
 *   3 elements  → Type II (α+γ two-phase)
 *   4 elements  → Type III (γ single phase)
 *   5+ elements → Type IV (amorphous / metastable)
 */
function deriveConfigType(
  composition: Readonly<Record<string, number>>,
): ConfigType {
  const soluteCount = Object.keys(composition).filter(
    (el) => el !== "U" && (composition[el] ?? 0) > 0,
  ).length
  if (soluteCount <= 2) return "type_i"
  if (soluteCount === 3) return "type_ii"
  if (soluteCount === 4) return "type_iii"
  return "type_iv"
}

/**
 * Compute B/V ratio from composition (B = group V total, V = group VI total).
 * Falls back to 0 if the denominator is zero.
 */
function deriveBvRatio(composition: Readonly<Record<string, number>>): number {
  const groupV =
    (composition["V"] ?? 0) +
    (composition["Nb"] ?? 0) +
    (composition["Ta"] ?? 0)
  const groupVI =
    (composition["Cr"] ?? 0) +
    (composition["Mo"] ?? 0) +
    (composition["W"] ?? 0)
  if (groupVI === 0) return 0
  return +(groupV / groupVI).toFixed(2)
}

function mapBackendToFrontend(
  response: OptimizeResponse,
): { pareto: ParetoSolution[]; convergence: ConvergenceData } {
  const pareto: ParetoSolution[] = response.pareto_front.map(
    (sol, index) => ({
      id: `sol-${String(index + 1).padStart(3, "0")}`,
      composition: JSON.stringify(sol.composition),
      uDensity: sol.objectives.u_density ?? 0,
      phaseStability: sol.objectives.phase_temp ?? 0,
      fabricability: sol.objectives.fabricability ?? 0,
      configType: deriveConfigType(sol.composition),
      bvRatio: deriveBvRatio(sol.composition),
      rank: sol.rank,
    }),
  )

  const convergence: ConvergenceData = {
    generationalDistance: response.convergence.gd_history.map((val, i) => ({
      generation: i + 1,
      value: val,
    })),
    hypervolume: response.convergence.hv_history.map((val, i) => ({
      generation: i + 1,
      value: val,
    })),
  }

  return { pareto, convergence }
}

export function useOptimization(): UseOptimizationReturn {
  const [state, setState] = useState<OptimizationState>("idle")
  const [paretoData, setParetoData] = useState<ParetoSolution[]>([])
  const [convergenceData, setConvergenceData] = useState<ConvergenceData>({
    generationalDistance: [],
    hypervolume: [],
  })
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState(0)
  const [generation, setGeneration] = useState(0)
  const [totalGenerations, setTotalGenerations] = useState(0)

  const progressTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const clearProgressTimer = useCallback(() => {
    if (progressTimerRef.current) {
      clearInterval(progressTimerRef.current)
      progressTimerRef.current = null
    }
  }, [])

  const startOptimization = useCallback(
    async (params: OptimizeRequest) => {
      clearProgressTimer()
      setState("loading")
      setParetoData([])
      setConvergenceData({ generationalDistance: [], hypervolume: [] })
      setError(null)
      setProgress(0)
      setGeneration(0)
      setTotalGenerations(params.algorithm?.n_gen ?? 100)

      // Simulate progress (backend runs synchronously, so we show
      // incremental progress to keep the UI responsive).
      progressTimerRef.current = setInterval(() => {
        setProgress((prev) => {
          const next = prev + Math.random() * 8 + 2
          if (next >= 95) {
            return 95
          }
          return next
        })
        setGeneration((prev) =>
          Math.min(prev + 1, params.algorithm?.n_gen ?? 100),
        )
      }, 300)

      try {
        const response = await runOptimization(params)
        clearProgressTimer()

        const { pareto, convergence } = mapBackendToFrontend(response)

        setParetoData(pareto)
        setConvergenceData(convergence)
        setProgress(100)
        setGeneration(params.algorithm?.n_gen ?? 100)
        setState("success")
      } catch (err: unknown) {
        clearProgressTimer()

        const message =
          err instanceof Error ? err.message : "Optimization failed"
        setError(message)
        setState("error")
      }
    },
    [clearProgressTimer],
  )

  const reset = useCallback(() => {
    clearProgressTimer()
    setState("idle")
    setParetoData([])
    setConvergenceData({ generationalDistance: [], hypervolume: [] })
    setError(null)
    setProgress(0)
    setGeneration(0)
    setTotalGenerations(0)
  }, [clearProgressTimer])

  return {
    state,
    paretoData,
    convergenceData,
    error,
    progress,
    generation,
    totalGenerations,
    startOptimization,
    reset,
  }
}
