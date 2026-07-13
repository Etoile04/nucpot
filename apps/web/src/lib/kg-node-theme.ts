/**
 * KG node-type visual tokens (NFM-1337 fix F3).
 *
 * Centralizes the Tailwind badge palette previously duplicated as inline
 * `TYPE_COLORS` records in `KgNodeDetailContent.tsx` and `KgSearchContent.tsx`.
 *
 * Why a separate module:
 *   - `graph-theme.ts` exposes raw hex strings consumed by D3 renderers and
 *     CSS custom-property bindings. The badge palette is Tailwind class
 *     strings consumed by JSX badges. Mixing them would couple two unrelated
 *     rendering layers.
 *   - All 6 KG node types (Material, Property, Experiment, Condition,
 *     Publication, Measurement) are richer than the 4 GraphNodeTypes the
 *     canvas public-API surfaces, so the canvas tokens cannot be reused
 *     directly without lossy mapping.
 *
 * Usage:
 *   import { kgNodeTypeClass } from "@/lib/kg-node-theme"
 *   <span className={kgNodeTypeClass(node.node_type)}>{node.node_type}</span>
 */

/** Canonical KG node-type strings used by the API and front-end badges. */
export const KG_NODE_TYPE_PALETTE = {
  Material: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  Property: "bg-green-500/20 text-green-300 border-green-500/30",
  Experiment: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  Condition: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  Publication: "bg-rose-500/20 text-rose-300 border-rose-500/30",
  Measurement: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
} as const satisfies Readonly<Record<string, string>>

export type KgNodeTypeName = keyof typeof KG_NODE_TYPE_PALETTE

/** All canonical KG node-type names. Useful for iteration in tests. */
export const KG_NODE_TYPE_NAMES = Object.keys(
  KG_NODE_TYPE_PALETTE,
) as readonly KgNodeTypeName[]

/** Fallback palette for unknown types — neutral gray. */
const FALLBACK_PALETTE =
  "bg-gray-500/20 text-gray-300 border-gray-500/30" as const

/**
 * Returns the Tailwind badge classes for a KG node type.
 * Unknown / future types fall back to a neutral gray so the badge still
 * renders without crashing.
 */
export function kgNodeTypeClass(type: string): string {
  return (
    (KG_NODE_TYPE_PALETTE as Readonly<Record<string, string>>)[type] ??
    FALLBACK_PALETTE
  )
}