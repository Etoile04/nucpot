/**
 * Shared graph utility functions used by both SvgRenderer and CanvasRenderer.
 */

import type { SimEdge } from "./types"

/** Compute 1-hop neighbor IDs for a given node from its edges. */
export function getNeighborIds(
  nodeId: string,
  edges: readonly SimEdge[],
): ReadonlySet<string> {
  const neighbors = new Set<string>()
  for (const edge of edges) {
    const src = typeof edge.source === "string" ? edge.source : edge.source.id
    const tgt = typeof edge.target === "string" ? edge.target : edge.target.id
    if (src === nodeId) neighbors.add(tgt)
    if (tgt === nodeId) neighbors.add(src)
  }
  return neighbors
}
