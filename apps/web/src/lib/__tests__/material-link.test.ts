/**
 * Pure-function unit tests for the NFM-4445 single-resolver hook
 * ({@link resolveMaterialLink}).  These tests have no React, no
 * jsdom, no graph rendering — they verify the decision contract
 * from CONTEXT.md §"图谱标识与列表":
 *
 *   - Non-Material nodes → `{ kind: "tooltip" }`.
 *   - Material node with `materials_id` bridge → `{ kind: "navigate", href: "/materials/{id}" }`.
 *   - Material node without bridge (NFM-4093 same-name cohort) → `{ kind: "tooltip" }`.
 *
 * Pure-function tests because the resolver is the canonical site of
 * the "全站唯一解析点" rule from the reopened decision packet; a
 * future contributor adding a new click handler must call this
 * function and respect its decision — these tests pin the contract.
 */

import { describe, expect, it } from "vitest"
import type { GraphNode } from "@/components/graph/types"
import { resolveMaterialLink } from "@/lib/material-link"

function node(partial: Partial<GraphNode>): GraphNode {
  return {
    id: "kg-uuid",
    label: "UO2",
    type: "material",
    ...partial,
  } as GraphNode
}

describe("resolveMaterialLink — NFM-4445 bridge routing", () => {
  it("routes Material node with materials_id bridge to /materials/{id}", () => {
    const decision = resolveMaterialLink(
      node({ type: "material", materials_id: "068dc946-9dd9-4a8d-bad0-9f24359b8b87" }),
    )
    expect(decision).toEqual({
      kind: "navigate",
      href: "/materials/068dc946-9dd9-4a8d-bad0-9f24359b8b87",
    })
  })

  it("returns tooltip for Material node WITHOUT materials_id (NFM-4093 same-name cohort)", () => {
    const decision = resolveMaterialLink(node({ type: "material", materials_id: undefined }))
    expect(decision).toEqual({ kind: "tooltip" })
  })

  it("returns tooltip for non-Material nodes (property, entity, default)", () => {
    for (const t of ["property", "entity", "default"] as const) {
      const decision = resolveMaterialLink(node({ type: t, materials_id: undefined }))
      expect(decision).toEqual({ kind: "tooltip" })
    }
  })

  it("never returns /materials/{kg_node_id} — KG-node UUIDs must not leak into the URL", () => {
    const decision = resolveMaterialLink(node({ type: "material", id: "kg-uuid-1234" }))
    // Even if materials_id is absent, decision is tooltip — never a /materials/{id} route.
    expect(decision.kind).toBe("tooltip")
    if (decision.kind === "navigate") {
      expect(decision.href.startsWith("/materials/kg-uuid-1234")).toBe(false)
    }
  })

  it("does not mutate the input node", () => {
    const original = node({ type: "material", materials_id: "abc" })
    const snapshot = { ...original }
    resolveMaterialLink(original)
    expect(original).toEqual(snapshot)
  })
})
