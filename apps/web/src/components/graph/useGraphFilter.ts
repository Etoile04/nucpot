/**
 * useGraphFilter — filter graph nodes by type.
 *
 * Takes the full GraphData and a set of active type filters,
 * returns only the visible nodes and their connected edges.
 */

import { useMemo } from "react"
import type { GraphData, GraphNodeType } from "./types"

export interface UseGraphFilterOptions {
  /** If provided, only nodes matching these types are shown. Empty = show all. */
  readonly activeTypes?: ReadonlySet<GraphNodeType>
}

export interface UseGraphFilterReturn {
  readonly filteredData: GraphData
  readonly activeTypes: ReadonlySet<GraphNodeType>
  readonly toggleType: (type: GraphNodeType) => void
  readonly resetFilter: () => void
  readonly allTypes: readonly GraphNodeType[]
}

const ALL_TYPES: readonly GraphNodeType[] = [
  "material",
  "property",
  "entity",
  "default",
] as const

export function useGraphFilter(
  data: GraphData,
  options: UseGraphFilterOptions = {},
): UseGraphFilterReturn {
  const activeTypes = options.activeTypes ?? new Set<GraphNodeType>(ALL_TYPES)

  const filteredData = useMemo(() => {
    if (activeTypes.size === 0 || activeTypes.size === ALL_TYPES.length) {
      return data
    }

    const visibleIds = new Set(
      data.nodes.filter((n) => activeTypes.has(n.type)).map((n) => n.id),
    )

    return {
      nodes: data.nodes.filter((n) => visibleIds.has(n.id)),
      edges: data.edges.filter(
        (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
      ),
    }
  }, [data, activeTypes])

  const toggleType = useMemo(() => {
    return (_type: GraphNodeType) => {
      const next = new Set(activeTypes)
      if (next.has(_type)) {
        next.delete(_type)
      } else {
        next.add(_type)
      }
      return next
    }
  }, [activeTypes])

  const resetFilter = () => new Set<GraphNodeType>(ALL_TYPES)

  return useMemo(
    () => ({
      filteredData,
      activeTypes,
      toggleType,
      resetFilter,
      allTypes: ALL_TYPES,
    }),
    [filteredData, activeTypes, toggleType],
  )
}
