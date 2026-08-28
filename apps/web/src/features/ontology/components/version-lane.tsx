/**
 * VersionLane: vertical timeline with dot markers for ontology versions.
 * Uses Tailwind classes — no inline styles.
 *
 * Accessibility (NFM-3800): the clickable version row is rendered as a real
 * <button> inside a <li>. We previously put role="button" on the <li>, which
 * Lighthouse flagged as an aria-allowed-role violation — role="button" is not
 * in the allowed ARIA roles for <li>.
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

interface RowContentsProps {
  readonly version: OntologyVersion
  readonly isSelected: boolean
  readonly dateStr: string
}

function RowContents({ version, isSelected, dateStr }: RowContentsProps) {
  const dotClass = STATUS_DOT_CLASSES[version.status] ?? 'border-gray-500'
  const versionClass = isSelected ? 'text-blue-400 font-semibold' : 'text-gray-200'
  return (
    <>
      {/* Dot marker */}
      <div
        className={`w-4 h-4 rounded-full border-2 flex-shrink-0 mt-0.5 relative z-[1] transition-all ${dotClass} ${isSelected ? 'bg-blue-500 shadow-[0_0_0_4px_rgba(59,130,246,0.25)]' : 'bg-gray-900'}`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`tabular-nums font-mono text-sm ${versionClass}`}>
            v{version.version}
          </span>
          <span className="text-xs text-gray-400 bg-gray-800 px-2 rounded-full">
            {STATUS_LABELS[version.status]}
          </span>
        </div>
        <div className="text-xs text-gray-500 mt-0.5 tabular-nums">
          {dateStr}{version.created_by ? ` · ${version.created_by}` : ''}
        </div>
        {version.changelog && (
          <p className="text-xs text-gray-500 mt-1 leading-tight m-0">
            {version.changelog}
          </p>
        )}
      </div>
    </>
  )
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
        const isInteractive = Boolean(onSelect) && !isSelected
        const dateStr = new Date(v.created_at).toLocaleDateString('zh-CN', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
        })
        const rowClass = v.status === 'deprecated' ? 'opacity-60' : ''

        return (
          <li
            key={v.id}
            aria-current={isSelected ? 'true' : undefined}
            className={`flex items-start gap-3 py-2 rounded transition-colors ${rowClass}`}
          >
            {isInteractive ? (
              <button
                type="button"
                onClick={() => onSelect?.(v.id)}
                className="flex items-start gap-3 w-full text-left bg-transparent border-0 p-0 m-0 rounded cursor-pointer hover:bg-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 focus-visible:ring-offset-gray-900 transition-colors"
              >
                <RowContents version={v} isSelected={false} dateStr={dateStr} />
              </button>
            ) : (
              <div
                className={`flex items-start gap-3 w-full ${isSelected ? 'bg-gray-800' : ''}`}
              >
                <RowContents version={v} isSelected={isSelected} dateStr={dateStr} />
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}