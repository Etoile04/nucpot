import { describe, it, expect, vi } from "vitest"
import { generateDefectCsv, generateFittingCsv, triggerDownload } from "../export-csv"
import type { DefectAnalysisResultResponse, PotentialFittingResultResponse } from "@/lib/md-verification-api"
import { DefectType, FittingMethod } from "@/lib/md-verification-api"

const MOCK_DEFECTS: DefectAnalysisResultResponse[] = [
  {
    id: "d1",
    verification_job_id: "j1",
    defect_type: DefectType.VACANCY,
    concentration: 0.0012,
    formation_energy: -3.45,
    metadata: null,
  },
  {
    id: "d2",
    verification_job_id: "j1",
    defect_type: DefectType.INTERSTITIAL,
    concentration: 0.0008,
    formation_energy: -2.10,
    metadata: null,
  },
]

const MOCK_FITTINGS: PotentialFittingResultResponse[] = [
  {
    id: "f1",
    verification_job_id: "j1",
    fitting_method: FittingMethod.ARC_DPA,
    parameters: { slope: 0.18, intercept: -0.005 },
    quality_metrics: { r_squared: 0.95 },
    created_at: "2026-01-01T00:00:00Z",
  },
]

describe("generateDefectCsv", () => {
  it("returns CSV string with BOM and Chinese headers", () => {
    const csv = generateDefectCsv(MOCK_DEFECTS)
    // BOM prefix for UTF-8 Excel compatibility
    expect(csv.startsWith("﻿")).toBe(true)
    expect(csv).toContain("缺陷类型")
    expect(csv).toContain("浓度")
    expect(csv).toContain("形成能 (eV)")
    expect(csv).toContain("空位")
    expect(csv).toContain("0.0012")
  })

  it("returns only header row for empty data", () => {
    const csv = generateDefectCsv([])
    const lines = csv.split("\n").filter(Boolean)
    // BOM line + header = 2 lines
    expect(lines.length).toBeGreaterThanOrEqual(1)
    expect(csv).toContain("缺陷类型")
  })
})

describe("generateFittingCsv", () => {
  it("returns CSV string with BOM and Chinese headers", () => {
    const csv = generateFittingCsv(MOCK_FITTINGS)
    expect(csv.startsWith("﻿")).toBe(true)
    expect(csv).toContain("拟合方法")
    expect(csv).toContain("参数")
    expect(csv).toContain("质量指标")
    expect(csv).toContain("arc-dpa")
  })

  it("returns only header row for empty data", () => {
    const csv = generateFittingCsv([])
    expect(csv).toContain("拟合方法")
    expect(csv.split("\n").filter(Boolean).length).toBeGreaterThanOrEqual(1)
  })
})

describe("triggerDownload", () => {
  it("creates a blob URL and triggers download", () => {
    const originalCreateObjectURL = URL.createObjectURL
    const originalRevokeObjectURL = URL.revokeObjectURL
    const createdUrls: string[] = []

    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    URL.createObjectURL = (_: Blob) => {
      const url = `blob:test-${createdUrls.length}`
      createdUrls.push(url)
      return url
    }
    URL.revokeObjectURL = (url: string) => {
      const idx = createdUrls.indexOf(url)
      if (idx >= 0) createdUrls.splice(idx, 1)
    }

    // Use a real anchor element so appendChild works
    const anchor = document.createElement("a")
    const spyClick = vi.spyOn(anchor, "click").mockImplementation(() => {})

    const originalCreateElement = document.createElement.bind(document)
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      if (tag === "a") return anchor
      return originalCreateElement(tag)
    })

    try {
      triggerDownload("test.csv", "hello,world\n")

      expect(anchor.download).toBe("test.csv")
      expect(anchor.href).toContain("blob:test-")
      expect(spyClick).toHaveBeenCalledOnce()
      expect(createdUrls.length).toBe(0) // revoked after click
    } finally {
      URL.createObjectURL = originalCreateObjectURL
      URL.revokeObjectURL = originalRevokeObjectURL
      vi.restoreAllMocks()
    }
  })
})
