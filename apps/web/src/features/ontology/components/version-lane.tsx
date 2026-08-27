/**
 * VersionLane: vertical timeline with dot markers for ontology versions.
 * Uses Tailwind classes — no inline styles.
 */
import type { OntologyVersion, OntologyVersionStatus } from '../types'
import { STATUS_LABELS } from '../types'

interface VersionLaneProps {
  readonly versions: readonly OntologyVersion[]
  readonly selectedId?: string
  readonly onSelect?: (id: string) => void
}

const STATUS_DOT_CLASSES: Record<OntologyVersionStatus, string> = {
  draft: 'border-amber-500',
  published: 'border-emerald-500',
  deprecated: 'border-gray-500',
}

export function VersionLane({ versions, selectedId, onSelect }: VersionLaneProps) {
  if (versions.length === 0) {
    return <p className="text-gray-400 text-sm">No versions recorded.</p>
  }

  return (
    <ol className="list-none m-0 p-0 relative" aria-label="Version history">
      {/* Vertical line */}
      <div
        className="absolute left-[7px] top-3 bottom-3 w-0.5 bg-gray-700"
        aria-hidden="true"
      />
      {versions.map((v) => {
        const isSelected = v.id === selectedId
        const dateStr = new Date(v.created_at).toLocaleDateString('zh-CN', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
        })
        return (
          <li
            key={v.id}
            aria-current={isSelected ? 'true' : undefined}
            className={`flex items-start gap-3 py-2 rounded transition-colors ${onSelect && !isSelected ? 'cursor-pointer hover:bg-gray-800' : ''} ${v.status === 'deprecated' ? 'opacity-60' : ''}`}
            onClick={isSelected || !onSelect ? undefined : () => onSelect(v.id)}
            onKeyDown={(e) => {
              if ((e.key === 'Enter' || e.key === ' ') && onSelect && !isSelected) {
                e.preventDefault()
                onSelect(v.id)
              }
            }}
            tabIndex={onSelect ? 0 : undefined}
            role={onSelect ? 'button' : undefined}
          >
            {/* Dot marker */}
            <div
              className={`w-4 h-4 rounded-full border-2 flex-shrink-0 mt-0.5 relative z-[1] transition-all ${STATUS_DOT_CLASSES[v.status] ?? 'border-gray-500'} ${isSelected ? 'bg-blue-500 shadow-[0_0_0_4px_rgba(59,130,246,0.25)]' : 'bg-gray-900'}`}
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className={`tabular-nums font-mono text-sm ${isSelected ? 'text-blue-400' : 'text-gray-200'} ${isSelected ? 'font-semibold' : ''}`}>
                  v{v.version}
                </span>
                <span className="text-xs text-gray-400 bg-gray-800 px-2 rounded-full">
                  {STATUS_LABELS[v.status]}
                </span>
              </div>
              <div className="text-xs text-gray-500 mt-0.5 tabular-nums">
                {dateStr}{v.created_by ? ` · ${v.created_by}` : ''}
              </div>
              {v.changelog && (
                <p className="text-xs text-gray-500 mt-1 leading-tight m-0">
                  {v.changelog}
                </p>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
