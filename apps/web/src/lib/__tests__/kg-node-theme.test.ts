/**
 * Tests for kg-node-theme (NFM-1337 fix F3).
 */

import { describe, it, expect } from "vitest"
import {
  KG_NODE_TYPE_PALETTE,
  KG_NODE_TYPE_NAMES,
  kgNodeTypeClass,
} from "../kg-node-theme"

describe("kg-node-theme", () => {
  it("1. exposes all six canonical KG node types in the palette", () => {
    expect(KG_NODE_TYPE_NAMES).toEqual([
      "Material",
      "Property",
      "Experiment",
      "Condition",
      "Publication",
      "Measurement",
    ])
    for (const name of KG_NODE_TYPE_NAMES) {
      expect(KG_NODE_TYPE_PALETTE[name]).toMatch(/^bg-/)
      expect(KG_NODE_TYPE_PALETTE[name]).toContain("border-")
    }
  })

  it("2. returns the correct Tailwind classes for a known type", () => {
    expect(kgNodeTypeClass("Material")).toBe(
      "bg-blue-500/20 text-blue-300 border-blue-500/30",
    )
    expect(kgNodeTypeClass("Publication")).toBe(
      "bg-rose-500/20 text-rose-300 border-rose-500/30",
    )
    expect(kgNodeTypeClass("Measurement")).toBe(
      "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
    )
  })

  it("3. falls back to a neutral gray palette for unknown types", () => {
    expect(kgNodeTypeClass("UnknownFutureType")).toBe(
      "bg-gray-500/20 text-gray-300 border-gray-500/30",
    )
    expect(kgNodeTypeClass("")).toBe(
      "bg-gray-500/20 text-gray-300 border-gray-500/30",
    )
  })

  it("4. is case-sensitive (only canonical capitalized names match)", () => {
    expect(kgNodeTypeClass("material")).toBe(
      "bg-gray-500/20 text-gray-300 border-gray-500/30",
    )
  })
})