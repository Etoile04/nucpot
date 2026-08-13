/**
 * KgToolbar — zoom controls and type filter dropdown for KG Explorer.
 *
 * Renders zoom in/out/fit buttons and a type filter combobox.
 * All buttons are keyboard accessible with native button behavior.
 */

"use client"

import type { GraphNodeType } from "@/components/graph/types"

const TYPE_OPTIONS: readonly GraphNodeType[] = [
  "material",
  "property",
  "entity",
  "default",
] as const

const TYPE_LABELS: Readonly<Record<GraphNodeType, string>> = {
  material: "Material",
  property: "Property",
  entity: "Entity",
  default: "Other",
}

interface KgToolbarProps {
  readonly onZoomIn: () => void
  readonly onZoomOut: () => void
  readonly onFit: () => void
  readonly onToggleType: (type: GraphNodeType) => void
  readonly activeTypes: ReadonlySet<GraphNodeType>
}

export function KgToolbar({
  onZoomIn,
  onZoomOut,
  onFit,
  onToggleType,
  activeTypes,
}: KgToolbarProps) {
  const activeCount = activeTypes.size

  return (
    <nav
      className="flex items-center gap-2 px-3 py-2"
      aria-label="Graph toolbar"
    >
      {/* Zoom controls */}
      <button
        type="button"
        aria-label="Zoom in"
        onClick={onZoomIn}
        className="rounded p-1.5 text-gray-400 hover:bg-gray-700 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <circle cx="8" cy="8" r="5" />
          <line x1="8" y1="1" x2="8" y2="4" />
          <line x1="8" y1="12" x2="8" y2="15" />
          <line x1="1" y1="8" x2="4" y2="8" />
          <line x1="12" y1="8" x2="15" y2="8" />
        </svg>
      </button>

      <button
        type="button"
        aria-label="Zoom out"
        onClick={onZoomOut}
        className="rounded p-1.5 text-gray-400 hover:bg-gray-700 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <line x1="1" y1="8" x2="15" y2="8" />
        </svg>
      </button>

      <button
        type="button"
        aria-label="Fit to view"
        onClick={onFit}
        className="rounded p-1.5 text-gray-400 hover:bg-gray-700 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <rect x="2" y="2" width="12" height="12" rx="1" />
        </svg>
      </button>

      {/* Separator */}
      <div
        className="mx-1 h-6 w-px bg-gray-600"
        aria-hidden="true"
      />

      {/* Type filter */}
      <label className="flex items-center gap-1.5 text-xs text-gray-400">
        <span className="sr-only">Filter by type</span>
        <select
          aria-label="Filter by type"
          className="rounded border border-gray-600 bg-gray-800 px-2 py-1 text-xs text-gray-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500"
          onChange={(e) => {
            const value = e.target.value as GraphNodeType
            onToggleType(value)
          }}
          value=""
        >
          <option value="" disabled>
            {activeCount} / {TYPE_OPTIONS.length} types
          </option>
          {TYPE_OPTIONS.map((type) => (
            <option key={type} value={type}>
              {TYPE_LABELS[type]}
              {activeTypes.has(type) ? " ✓" : ""}
            </option>
          ))}
        </select>
      </label>
    </nav>
  )
}
