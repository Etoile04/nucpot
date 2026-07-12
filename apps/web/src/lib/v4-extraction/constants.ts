/**
 * Constants for the V4 Extraction frontend.
 *
 * Color maps, label maps, category lists, and canonical phases
 * as specified in the UX design specification (NFM-557).
 */

import type {
  Confidence,
  JobStatus,
  StagingStatus,
  CacheLevel,
  SourceType,
  Priority,
} from "./types"

// ─── Property Categories ─────────────────────────────────────────

export const PROPERTY_CATEGORIES = [
  { value: "density", label: "密度 / Density" },
  { value: "specific_heat", label: "比热容 / Specific Heat" },
  {
    value: "thermal_conductivity",
    label: "热传导率 / Thermal Conductivity",
  },
  {
    value: "elastoplastic",
    label: "弹塑性模型 / Elastoplastic",
  },
  {
    value: "thermal_expansion",
    label: "热膨胀 / Thermal Expansion",
  },
  {
    value: "irradiation_creep",
    label: "辐照蠕变 / Irradiation Creep",
  },
  {
    value: "irradiation_swelling",
    label: "辐照肿胀 / Irradiation Swelling",
  },
  { value: "corrosion", label: "腐蚀 / Corrosion" },
  { value: "hardening", label: "硬化性能 / Hardening" },
  {
    value: "material_spec",
    label: "材料规格 / Material Spec",
  },
  { value: "other", label: "其他 / Other" },
] as const

// ─── Canonical Phases ─────────────────────────────────────────────

export const CANONICAL_PHASES = [
  "alpha",
  "beta",
  "gamma",
  "delta",
  "epsilon",
  "liquid",
  "gas",
  "amorphous",
  "mixed",
  "unknown",
  "fcc",
  "bcc",
  "hcp",
  "tetragonal",
  "orthorhombic",
  "cubic",
  "monoclinic",
  "hexagonal",
  "rhombohedral",
] as const

// ─── Job Status ────────────────────────────────────────────────

export const JOB_STATUS_COLORS: Record<JobStatus, string> = {
  queued: "default",
  running: "processing",
  extracting: "blue",
  mapping: "blue",
  quality_gate: "warning",
  completed: "success",
  partial: "warning",
  failed: "error",
}

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: "排队中",
  running: "运行中",
  extracting: "提取中",
  mapping: "映射中",
  quality_gate: "质量检查",
  completed: "完成",
  partial: "部分完成",
  failed: "失败",
}

export const JOB_STATUS_TEXT_COLORS: Record<JobStatus, string> = {
  queued: "rgba(0,0,0,0.45)",
  running: "#1890ff",
  extracting: "#1890ff",
  mapping: "#1890ff",
  quality_gate: "#faad14",
  completed: "#52c41a",
  partial: "#faad14",
  failed: "#ff4d4f",
}

// Maps job status to the current step index for the Steps component
export const JOB_STATUS_STEP_MAP: Record<JobStatus, number> = {
  queued: 0,
  running: 1,
  extracting: 2,
  mapping: 3,
  quality_gate: 4,
  completed: 5,
  partial: 5,
  failed: -1, // handled separately
}

// Terminal statuses that stop polling
export const TERMINAL_STATUSES: JobStatus[] = [
  "completed",
  "partial",
  "failed",
]

// ─── Confidence ──────────────────────────────────────────────────

export const CONFIDENCE_COLORS: Record<Confidence, string> = {
  high: "success",
  medium: "warning",
  low: "error",
}

export const CONFIDENCE_LABELS: Record<Confidence, string> = {
  high: "高 / High",
  medium: "中 / Medium",
  low: "低 / Low",
}

// ─── Staging Status ─────────────────────────────────────────────

export const STAGING_STATUS_COLORS: Record<StagingStatus, string> = {
  pending: "processing",
  approved: "success",
  rejected: "error",
  promoted: "purple",
}

export const STAGING_STATUS_LABELS: Record<StagingStatus, string> = {
  pending: "待审核",
  approved: "已批准",
  rejected: "已拒绝",
  promoted: "已入库",
}

// ─── Cache Level ─────────────────────────────────────────────────

export const CACHE_LEVEL_COLORS: Record<CacheLevel, string> = {
  L1: "green",
  L2: "blue",
  L3A: "orange",
  L3B: "geekblue",
}

export const CACHE_LEVEL_LABELS: Record<CacheLevel, string> = {
  L1: "L1 直测",
  L2: "L2 文献",
  L3A: "L3A 插值",
  L3B: "L3B 模拟",
}

// ─── Source Type ───────────────────────────────────────────────

export const SOURCE_TYPE_COLORS: Record<SourceType, string> = {
  doi: "blue",
  url: "cyan",
  file: "purple",
  internal_id: "default",
}

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  doi: "DOI",
  url: "URL",
  file: "文件路径",
  internal_id: "内部ID",
}

// ─── Priority ───────────────────────────────────────────────────

export const PRIORITY_COLORS: Record<Priority, string> = {
  normal: "default",
  high: "red",
}

export const PRIORITY_LABELS: Record<Priority, string> = {
  normal: "普通 / Normal",
  high: "高 / High",
}

// ─── Polling intervals ──────────────────────────────────────────

export const STATUS_POLL_INTERVAL_MS = 3000
export const BADGE_POLL_INTERVAL_MS = 30_000

// ─── Element system presets ─────────────────────────────────────

export const ELEMENT_SYSTEM_PRESETS = [
  { value: "UO2", label: "UO₂" },
  { value: "Zr-Nb", label: "Zr-Nb" },
  { value: "U-Pu-Zr", label: "U-Pu-Zr" },
  { value: "UN", label: "UN" },
]
