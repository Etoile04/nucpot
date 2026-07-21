/**
 * Type definitions for the Composition Design Workbench.
 *
 * NFM-1668 §7 + NFM-1673 §9 + NFM-1696 (API types)
 *
 * Two sections:
 *   1. UI domain types (objective keys, config types, chart shapes)
 *   2. Backend API request/response types (mirror backend Pydantic schemas)
 */

// =============================================================================
// §1  UI domain types
// =============================================================================

/** Optimization objective identifiers */
type ObjectiveKey = "u_density" | "phase_stability" | "fabricability"

/** Configuration type identifiers */
type ConfigType = "type_i" | "type_ii" | "type_iii" | "type_iv"

/** Objective weight state (0-100 each) */
interface ObjectiveWeights {
  u_density: number
  phase_stability: number
  fabricability: number
}

/** Design constraints */
interface DesignConstraints {
  uContentMin: number
  uContentMax: number
  singleElementCeiling: number
  totalAddedElements: number
  bvRatioMin: number
  bvRatioMax: number
  configTypes: ConfigType[]
  densityLowerBound?: number
  thermalConductivityMin?: number
  maxDpa?: number
}

/** A single Pareto solution point */
interface ParetoSolution {
  id: string
  composition: string
  uDensity: number
  phaseStability: number
  fabricability: number
  configType: ConfigType
  bvRatio: number
  rank: number
}

/** Optimization run state */
interface OptimizationState {
  status: "idle" | "running" | "completed" | "error"
  progress?: number
  generation?: number
  error?: string
}

/** Axis combination for Pareto scatter */
interface AxisPair {
  x: ObjectiveKey
  y: ObjectiveKey
}

/** Convergence data point */
interface ConvergencePoint {
  generation: number
  value: number
}

/** Convergence metrics for a single optimization run */
interface ConvergenceData {
  generationalDistance: ConvergencePoint[]
  hypervolume: ConvergencePoint[]
}

/** An objective with its label metadata for chart rendering */
interface ObjectiveMeta {
  zh: string
  en: string
  unit: string
}

// =============================================================================
// §2  Backend API request/response types
// =============================================================================

// --- Prediction feature input (mirrors PredictionFeatures) ---

/** 8 physical features computed from alloy composition. */
interface PredictionFeatures {
  readonly mo_equivalent: number
  readonly pauling_chi_diff: number
  readonly allen_chi_diff: number
  readonly config_entropy: number
  readonly bv_ratio: number
  readonly u_density: number
  readonly mixing_enthalpy: number
  readonly lattice_distortion: number
}

// --- Shared prediction output fields (mirrors PredictionWarningItem) ---

/** A warning generated during prediction. */
interface PredictionWarningItem {
  readonly code: string
  readonly message: string
}

// --- Phase classification (mirrors PhasePredictRequest/Response) ---

/** Request body for POST /api/v1/predict/phase. */
type PhasePredictRequest = PredictionFeatures

/** Probability for a single class. */
interface PhaseProbabilityItem {
  readonly class_label: string
  readonly probability: number
}

/** Response body for phase classification prediction. */
interface PhasePredictResponse {
  readonly predicted_phase: string
  readonly predicted_phase_label: string
  readonly probabilities: readonly PhaseProbabilityItem[]
  readonly confidence: number
  readonly warnings: readonly PredictionWarningItem[]
  readonly model_version: string
}

// --- Temperature prediction (mirrors TempPredictRequest/Response) ---

/** Request body for POST /api/v1/predict/temperature. */
type TempPredictRequest = PredictionFeatures

/** Response body for transition temperature prediction. */
interface TempPredictResponse {
  readonly predicted_temp_c: number
  readonly confidence_lower_c: number
  readonly confidence_upper_c: number
  readonly gpr_predicted_temp_c: number | null
  readonly svr_predicted_temp_c: number | null
  readonly confidence: number
  readonly warnings: readonly PredictionWarningItem[]
  readonly model_version: string
}

// --- NSGA-II optimization (mirrors OptimizeRequest/Response) ---

/** Weights for the three optimization objectives. */
interface ApiObjectiveWeights {
  readonly u_density?: number
  readonly phase_temp?: number
  readonly fabricability?: number
}

/** Search-space constraints for the alloy composition. */
interface OptimizationConstraints {
  readonly u_min?: number
  readonly u_max?: number
  readonly max_single_element?: number
  readonly n_elements?: readonly [number, number]
  readonly bv_ratio?: readonly [number, number]
}

/** NSGA-II algorithm hyperparameters. */
interface AlgorithmParams {
  readonly pop_size?: number
  readonly n_gen?: number
  readonly seed?: number | null
}

/** Request body for POST /api/v1/design/optimize. */
interface OptimizeRequest {
  readonly objectives?: ApiObjectiveWeights
  readonly constraints?: OptimizationConstraints
  readonly algorithm?: AlgorithmParams
}

/** A single Pareto-optimal composition with objective values. */
interface ApiParetoSolution {
  readonly composition: Readonly<Record<string, number>>
  readonly objectives: Readonly<Record<string, number>>
  readonly rank: number
}

/** Per-generation convergence indicator histories. */
interface ConvergenceMetrics {
  readonly gd_history: readonly number[]
  readonly hv_history: readonly number[]
}

/** Response body for the optimization endpoint. */
interface OptimizeResponse {
  readonly pareto_front: readonly ApiParetoSolution[]
  readonly convergence: ConvergenceMetrics
  readonly n_solutions: number
  readonly compute_time_ms: number
  readonly algorithm_params: AlgorithmParams
  readonly warnings: readonly string[]
}

export type {
  // §1  UI domain types
  ObjectiveKey,
  ConfigType,
  ObjectiveWeights,
  DesignConstraints,
  ParetoSolution,
  OptimizationState,
  AxisPair,
  ConvergencePoint,
  ConvergenceData,
  ObjectiveMeta,
  // §2  Backend API types
  PredictionFeatures,
  PredictionWarningItem,
  PhasePredictRequest,
  PhaseProbabilityItem,
  PhasePredictResponse,
  TempPredictRequest,
  TempPredictResponse,
  ApiObjectiveWeights,
  OptimizationConstraints,
  AlgorithmParams,
  OptimizeRequest,
  ApiParetoSolution,
  ConvergenceMetrics,
  OptimizeResponse,
}
