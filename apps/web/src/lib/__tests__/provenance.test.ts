/**
 * Tests for provenance badge utility (NFM-2217).
 *
 * Verifies:
 * - Token-to-badge mapping (llm, manual, mineru)
 * - Multi-token precedence: manual > mineru > llm
 * - Empty / unknown provenance handling
 * - Unrecognised tokens are ignored
 * - Section order and labels
 */

import {
  resolveProvenanceBadge,
  resolveProvenanceKey,
  getProvenanceSectionLabel,
  getProvenanceColor,
  KG_EDGE_BADGE,
  PROVENANCE_SECTION_ORDER,
} from "@/lib/provenance"

import { describe, it, expect } from "vitest"

describe("resolveProvenanceBadge", () => {
  it("maps 'llm' to LLM提取 (blue)", () => {
    const badge = resolveProvenanceBadge(["llm"])
    expect(badge).toEqual({ label: "LLM提取", color: "blue" })
  })

  it("maps 'manual' to 手动 (orange)", () => {
    const badge = resolveProvenanceBadge(["manual"])
    expect(badge).toEqual({ label: "手动", color: "orange" })
  })

  it("maps 'mineru' to MinerU图 (green)", () => {
    const badge = resolveProvenanceBadge(["mineru"])
    expect(badge).toEqual({ label: "MinerU图", color: "green" })
  })

  it("returns 来源未知 for empty provenance", () => {
    const badge = resolveProvenanceBadge([])
    expect(badge).toEqual({ label: "来源未知", color: "default" })
  })

  it("returns 来源未知 for unrecognised tokens", () => {
    const badge = resolveProvenanceBadge(["unknown_method", "foobar"])
    expect(badge).toEqual({ label: "来源未知", color: "default" })
  })

  it("applies manual > mineru > llm precedence", () => {
    expect(resolveProvenanceBadge(["llm", "manual"])).toEqual({
      label: "手动",
      color: "orange",
    })
  })

  it("applies manual > mineru precedence", () => {
    expect(resolveProvenanceBadge(["mineru", "manual"])).toEqual({
      label: "手动",
      color: "orange",
    })
  })

  it("applies mineru > llm precedence", () => {
    expect(resolveProvenanceBadge(["llm", "mineru"])).toEqual({
      label: "MinerU图",
      color: "green",
    })
  })

  it("handles all three tokens with correct precedence", () => {
    expect(resolveProvenanceBadge(["llm", "manual", "mineru"])).toEqual({
      label: "手动",
      color: "orange",
    })
  })

  it("ignores unrecognised tokens mixed with valid ones", () => {
    expect(resolveProvenanceBadge(["llm", "garbage", "manual"])).toEqual({
      label: "手动",
      color: "orange",
    })
  })

  it("is case-sensitive (backend normalises to lowercase)", () => {
    const badge = resolveProvenanceBadge(["LLM"])
    expect(badge).toEqual({ label: "来源未知", color: "default" })
  })
})

describe("resolveProvenanceKey", () => {
  it("returns 'llm' for single llm token", () => {
    expect(resolveProvenanceKey(["llm"])).toBe("llm")
  })

  it("returns 'manual' for multi-token with manual", () => {
    expect(resolveProvenanceKey(["llm", "manual"])).toBe("manual")
  })

  it("returns 'unknown' for empty array", () => {
    expect(resolveProvenanceKey([])).toBe("unknown")
  })

  it("returns 'unknown' for unrecognised tokens", () => {
    expect(resolveProvenanceKey(["foo"])).toBe("unknown")
  })
})

describe("getProvenanceSectionLabel", () => {
  it("returns correct labels for all known keys", () => {
    expect(getProvenanceSectionLabel("llm")).toBe("LLM提取")
    expect(getProvenanceSectionLabel("manual")).toBe("手动")
    expect(getProvenanceSectionLabel("mineru")).toBe("MinerU图")
    expect(getProvenanceSectionLabel("unknown")).toBe("来源未知")
  })
})

describe("getProvenanceColor", () => {
  it("returns correct colors for all known keys", () => {
    expect(getProvenanceColor("llm")).toBe("blue")
    expect(getProvenanceColor("manual")).toBe("orange")
    expect(getProvenanceColor("mineru")).toBe("green")
    expect(getProvenanceColor("unknown")).toBe("default")
  })
})

describe("KG_EDGE_BADGE", () => {
  it("has correct label and color", () => {
    expect(KG_EDGE_BADGE).toEqual({ label: "KG关系", color: "purple" })
  })
})

describe("PROVENANCE_SECTION_ORDER", () => {
  it("contains all provenance keys plus unknown", () => {
    expect(PROVENANCE_SECTION_ORDER).toEqual([
      "llm",
      "mineru",
      "manual",
      "unknown",
    ])
  })
})
