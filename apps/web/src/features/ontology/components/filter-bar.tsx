/**
 * FilterBar: status filter pills for the ontology list page.
 * Prop-driven (no router dependency) for testability.
 */
import { VERSION_STATUSES, STATUS_LABELS } from '../types'
import type { OntologyVersionStatus } from '../types'

interface FilterBarProps {
  readonly current: OntologyVersionStatus | 'all'
  readonly onChange: (status: OntologyVersionStatus | 'all') => void
  readonly statusCounts?: Record<string, number>
}

const ALL_OPTION = 'all' as const

export function FilterBar({ current, onChange, statusCounts }: FilterBarProps) {
  const options: Array<{ value: OntologyVersionStatus | 'all'; label: string }> = [
    { value: ALL_OPTION, label: 'All' },
    ...VERSION_STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] })),
  ]

  return (
    // NOTE: a11y (NFM-3794) — `<nav>` already carries the landmark role, so
    // a `role="group"` overlay is rejected by `aria-allowed-role` (the ARIA
    // spec only permits `group` on a small set of inline elements, not nav).
    // The aria-label below is sufficient to name the landmark for AT users.
    <nav
      className="onto-filter-bar flex flex-wrap items-center gap-2 px-4 py-3 bg-gray-800 rounded sticky top-0 z-10"
      aria-label="Status filter"
    >
      {options.map((opt) => {
        const isActive = current === opt.value
        const count = opt.value === ALL_OPTION
          ? Object.values(statusCounts ?? {}).reduce((a, b) => a + b, 0)
          : (statusCounts ?? {})[opt.value]
        return (
          <button
            key={opt.value}
            aria-pressed={isActive}
            onClick={() => onChange(opt.value)}
            className={`inline-flex items-center gap-1 px-3 py-1 rounded-full border text-sm cursor-pointer transition-colors ${
              isActive
                ? 'bg-blue-600 border-blue-600 text-white'
                : 'border-gray-600 bg-transparent text-gray-200 hover:bg-gray-700'
            }`}
          >
            {opt.label}
            {count !== undefined && (
              <span className="tabular-nums opacity-60 text-xs">{count}</span>
            )}
          </button>
        )
      })}
    </nav>
  )
}
