/**
 * Shared types, constants, and initial state for the 3-step task submission wizard.
 */

import type { PotentialSummary } from "@/lib/potentials-api"

// ---------------------------------------------------------------------------
// Step metadata
// ---------------------------------------------------------------------------

export const WIZARD_STEP_TITLES = [
  { title: "选择势函数", description: "从势函数库中搜索并选择" },
  { title: "配置模拟参数", description: "设置模拟参数与 HPC 后端" },
  { title: "确认并提交", description: "检查摘要并提交任务" },
] as const

export type WizardStepIndex = 0 | 1 | 2

// ---------------------------------------------------------------------------
// Dropdown / option constants
// ---------------------------------------------------------------------------

export const HPC_BACKEND_OPTIONS = [
  { value: "local", label: "本地计算" },
  { value: "tianjin", label: "天津超算 (天河)" },
  { value: "guangzhou", label: "广州超算 (天河二号)" },
] as const

export const ENSEMBLE_OPTIONS = [
  { value: "NPT", label: "NPT (等温等压)" },
  { value: "NVT", label: "NVT (等温等容)" },
  { value: "NVE", label: "NVE (微正则)" },
] as const

export const PHASE_OPTIONS = [
  { value: "BCC", label: "BCC (体心立方)" },
  { value: "FCC", label: "FCC (面心立方)" },
  { value: "HCP", label: "HCP (密排六方)" },
  { value: "other", label: "其他" },
] as const

export const DEFECT_TYPE_OPTIONS = [
  { value: "vacancy", label: "空位" },
  { value: "interstitial", label: "间隙原子" },
  { value: "dislocation", label: "位错" },
  { value: "grain_boundary", label: "晶界" },
  { value: "other", label: "其他" },
] as const

/** Human-readable labels keyed by defect-type value. */
export const DEFECT_TYPE_LABELS: Record<string, string> = {
  vacancy: "空位",
  interstitial: "间隙原子",
  dislocation: "位错",
  grain_boundary: "晶界",
  other: "其他",
}

// ---------------------------------------------------------------------------
// Form data
// ---------------------------------------------------------------------------

export interface WizardFormData {
  /** Step 1 – selected potential from library */
  selectedPotential: PotentialSummary | null
  elementSystem: string
  phase: string

  /** Step 2 – simulation parameters */
  structureFile: string
  temperature: number
  pressure: number
  simulationTime: number
  timestep: number
  ensemble: string
  defectTypes: string[]
  hpcBackend: string
  priority: number
}

export const INITIAL_WIZARD_FORM_DATA: WizardFormData = {
  selectedPotential: null,
  elementSystem: "U",
  phase: "BCC",
  structureFile: "",
  temperature: 300,
  pressure: 0,
  simulationTime: 100,
  timestep: 0.001,
  ensemble: "NPT",
  defectTypes: [],
  hpcBackend: "",
  priority: 5,
}
