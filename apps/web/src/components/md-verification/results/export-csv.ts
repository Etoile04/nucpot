/**
 * CSV export utilities for MD verification results.
 *
 * All generated CSVs include a UTF-8 BOM prefix (﻿)
 * so that Excel correctly renders Chinese headers.
 */

import type {
  DefectAnalysisResultResponse,
  PotentialFittingResultResponse,
} from "@/lib/md-verification-api"

/** Defect type labels for CSV Chinese headers */
const DEFECT_TYPE_LABELS: Record<string, string> = {
  vacancy: "空位",
  interstitial: "间隙原子",
  dislocation: "位错",
  grain_boundary: "晶界",
  other: "其他",
}

const BOM = "﻿"

/**
 * Generate a CSV string from defect analysis results.
 * Columns: 缺陷类型, 浓度, 形成能 (eV)
 */
export function generateDefectCsv(data: DefectAnalysisResultResponse[]): string {
  const header = ["缺陷类型", "浓度", "形成能 (eV)"].join(",")
  const rows = data.map((d) =>
    [
      DEFECT_TYPE_LABELS[d.defect_type] ?? d.defect_type,
      d.concentration,
      d.formation_energy ?? "",
    ].join(","),
  )
  return [BOM, header, ...rows].join("\n")
}

/**
 * Generate a CSV string from potential fitting results.
 * Columns: 拟合方法, 参数, 质量指标
 */
export function generateFittingCsv(data: PotentialFittingResultResponse[]): string {
  const header = ["拟合方法", "参数", "质量指标"].join(",")
  const rows = data.map((f) =>
    [
      f.fitting_method,
      JSON.stringify(f.parameters),
      f.quality_metrics ? JSON.stringify(f.quality_metrics) : "",
    ].join(","),
  )
  return [BOM, header, ...rows].join("\n")
}

/**
 * Trigger a browser file download for the given CSV content.
 * Creates a temporary Blob URL, clicks a hidden anchor, then revokes.
 */
export function triggerDownload(filename: string, csvContent: string): void {
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8" })
  const url = URL.createObjectURL(blob)

  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.style.display = "none"
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)

  URL.revokeObjectURL(url)
}
