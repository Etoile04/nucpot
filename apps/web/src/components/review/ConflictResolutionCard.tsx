"use client"

import { useState, useCallback } from "react"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import {
  type ConflictItem,
  type ConflictSource,
  type ConflictResolutionAction,
} from "@/lib/kg-review-api"

// ── Props ─────────────────────────────────────────────────────────────

interface ConflictResolutionCardProps {
  readonly conflict: ConflictItem
  readonly onResolve: (
    conflictId: string,
    action: ConflictResolutionAction,
  ) => void
  readonly loading?: boolean
}

export { type ConflictResolutionCardProps }

// ── Source Row ────────────────────────────────────────────────────────

interface SourceRowProps {
  readonly source: ConflictSource
  readonly label: string
  readonly selected: boolean
  readonly onSelect: () => void
}

function SourceRow({ source, label, selected, onSelect }: SourceRowProps) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={[
        "flex items-center gap-3 p-2 rounded w-full text-left transition-all",
        selected
          ? "bg-emerald-900/20 ring-2 ring-emerald-400"
          : "bg-gray-900/50 hover:bg-gray-900",
      ].join(" ")}
      role="radio"
      aria-checked={selected}
    >
      <span className="text-xs text-gray-500 w-12 shrink-0">{label}</span>
      <span className="text-xs text-gray-400 truncate">{source.sourceTitle}</span>
      <span className="text-sm text-gray-100 font-medium">
        {source.value}
        {source.unit != null && <span> {source.unit}</span>}
      </span>
      <span className="ml-auto">
        <ConfidenceBadge value={source.confidence} />
      </span>
    </button>
  )
}

// ── Card ───────────────────────────────────────────────────────────────

export function ConflictResolutionCard({
  conflict,
  onResolve,
  loading = false,
}: ConflictResolutionCardProps) {
  const [selectedSource, setSelectedSource] = useState<"a" | "b" | null>(null)

  const handleAction = useCallback(
    (action: ConflictResolutionAction) => {
      onResolve(conflict.id, action)
    },
    [conflict.id, onResolve],
  )

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 space-y-3">
      <h3 className="text-sm font-semibold text-amber-400">
        {conflict.entityName} {conflict.property} — 属性冲突 #
        {conflict.conflictNumber}
      </h3>

      <fieldset>
        <legend className="sr-only">
          选择冲突来源：{conflict.sourceA.sourceTitle} 或 {conflict.sourceB.sourceTitle}
        </legend>
        <div
          className="space-y-2"
          role="radiogroup"
          aria-label="冲突来源选择"
        >
          <SourceRow
            source={conflict.sourceA}
            label="来源 A"
            selected={selectedSource === "a"}
            onSelect={() => setSelectedSource("a")}
          />
          <SourceRow
            source={conflict.sourceB}
            label="来源 B"
            selected={selectedSource === "b"}
            onSelect={() => setSelectedSource("b")}
          />
        </div>
      </fieldset>

      <div className="flex gap-2 mt-2 flex-wrap">
        <button
          type="button"
          onClick={() => handleAction("keep_a")}
          disabled={loading}
          className="px-3 py-1.5 rounded text-sm bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-50 transition-colors"
          aria-label="选择来源 A"
        >
          选择 A
        </button>
        <button
          type="button"
          onClick={() => handleAction("keep_b")}
          disabled={loading}
          className="px-3 py-1.5 rounded text-sm bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-50 transition-colors"
          aria-label="选择来源 B"
        >
          选择 B
        </button>
        <button
          type="button"
          onClick={() => handleAction("not_conflict")}
          disabled={loading}
          className="px-3 py-1.5 rounded text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 disabled:opacity-50 transition-colors"
          aria-label="标记为非冲突"
        >
          标记为非冲突
        </button>
        <button
          type="button"
          onClick={() => handleAction("skip")}
          disabled={loading}
          className="px-3 py-1.5 rounded text-sm text-gray-400 hover:text-gray-200 disabled:opacity-50 transition-colors"
          aria-label="跳过此冲突"
        >
          跳过
        </button>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-2">
          <span className="text-gray-400 text-sm animate-pulse">
            处理中…
          </span>
        </div>
      )}
    </div>
  )
}
