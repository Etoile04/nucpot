/**
 * Single source of truth for resolving a graph node click to a
 * materials-page navigation decision.  NFM-4445 — backend is the
 * owner of the bridge between the KG-node id space and the
 * materials.id space; the frontend must never reconstruct that
 * mapping locally (e.g. via prefix stripping or label-to-id
 * fallback).  Use {@link resolveMaterialLink} from every click
 * handler that wants to turn a graph node into a materials-page
 * route; the function returns either an `href` to push or a
 * `tooltip` marker indicating the caller should surface a
 * tooltip instead.
 *
 * Contract (mirrors CONTEXT.md "图谱标识与列表"):
 *   - Non-Material nodes → `{ kind: "tooltip" }` (never navigate to a materials page).
 *   - Material node with `materials_id` bridge → `{ kind: "navigate", href: "/materials/{id}" }`.
 *   - Material node without bridge (NFM-4093 same-name cohort) → `{ kind: "tooltip" }`.
 *
 * Adding a new resolver site?  Read CONTEXT.md §"图谱标识与列表" first,
 * then call this function — do not branch on `node.type === "material"`
 * and read `node.materials_id` yourself.
 */

import type { GraphNode } from "@/components/graph/types"

/** Decision returned by {@link resolveMaterialLink}. */
export type MaterialLinkResolution =
  | { readonly kind: "navigate"; readonly href: string }
  | { readonly kind: "tooltip" }

/**
 * Resolve a {@link GraphNode} click to a materials-page navigation
 * decision.  Returns `{ kind: "tooltip" }` for non-Material nodes and
 * for Material nodes that lack a server-supplied bridge.
 *
 * The caller is responsible for invoking `router.push(resolution.href)`
 * on the `navigate` branch and rendering a tooltip on the `tooltip`
 * branch — this function is a pure mapping with no side effects.
 */
export function resolveMaterialLink(node: GraphNode): MaterialLinkResolution {
  if (node.type !== "material") {
    return { kind: "tooltip" }
  }
  const targetId = node.materials_id
  if (!targetId) {
    return { kind: "tooltip" }
  }
  return { kind: "navigate", href: `/materials/${targetId}` }
}
