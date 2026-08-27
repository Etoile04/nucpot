/**
 * VersionLane: vertical timeline with dot markers for ontology versions.
 *
 * Per NFM-3550 §2 and §3.2 — each node = version snapshot.
 * Selected version gets paper-warm halo.
 */
'use client'

import type { OntologyVersion, OntologyVersionStatus } from '../types'
import { STATUS_LABELS, STATUS_LABELS_ZH } from '../types'

interface VersionLaneProps {
  readonly versions: readonly OntologyVersion[]
  readonly selectedId?: string
  readonly onSelect?: (id: string) => void
}

const STATUS_COLORS: Record<OntologyVersionStatus, string> = {
  draft: 'var(--onto-status-draft)',
  published: 'var(--onto-status-published)',
  deprecated: 'var(--onto-status-deprecated)',
}

export function VersionLane({ versions, selectedId, onSelect }: VersionLaneProps) {
  if (versions.length === 0) {
    return (
      <p style={{ color: 'var(--onto-ink-muted)', fontSize: 'var(--onto-fs-sm)' }}>
        No versions recorded.
      </p>
    )
  }

  return (
    <ol
      style={{
        listStyle: 'none',
        margin: 0,
        padding: 0,
        position: 'relative',
      }}
      aria-label="Version history"
    >
      {/* Vertical line */}
      <div
        style={{
          position: 'absolute',
          left: 7,
          top: 12,
          bottom: 12,
          width: 2,
          backgroundColor: 'var(--onto-border-soft)',
        }}
        aria-hidden="true"
      />
      {versions.map((v, idx) => {
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
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 'var(--onto-space-3)',
              padding: 'var(--onto-space-2) 0',
              cursor: onSelect ? 'pointer' : 'default',
              opacity: v.status === 'deprecated' ? 0.6 : 1,
              borderRadius: 'var(--onto-radius-sm)',
              transition: `background var(--onto-dur-fast) var(--onto-ease-out)`,
            }}
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
              style={{
                width: 16,
                height: 16,
                borderRadius: '50%',
                border: `2px solid ${STATUS_COLORS[v.status] ?? 'var(--onto-border-strong)'}`,
                backgroundColor: isSelected ? 'var(--onto-accent)' : 'var(--onto-surface-0)',
                flexShrink: 0,
                marginTop: 2,
                boxShadow: isSelected ? '0 0 0 4px rgba(217, 162, 95, 0.25)' : 'none',
                transition: `all var(--onto-dur-base) var(--onto-ease-out)`,
                position: 'relative',
                zIndex: 1,
              }}
              aria-hidden="true"
            />
            {/* Content */}
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--onto-space-2)' }}>
                <span
                  style={{
                    fontFeatureSettings: '"tnum" 1',
                    fontFamily: 'var(--onto-font-mono)',
                    fontSize: 'var(--onto-fs-sm)',
                    color: isSelected ? 'var(--onto-accent)' : 'var(--onto-ink-default)',
                    fontWeight: isSelected ? 600 : 400,
                  }}
                >
                  v{v.version}
                </span>
                <span
                  style={{
                    fontSize: 'var(--onto-fs-xs)',
                    color: STATUS_COLORS[v.status] ?? 'var(--onto-ink-muted)',
                    backgroundColor: 'var(--onto-surface-2)',
                    padding: '1px var(--onto-space-2)',
                    borderRadius: 'var(--onto-radius-pill)',
                  }}
                >
                  {STATUS_LABELS[v.status]}
                </span>
              </div>
              <div
                style={{
                  fontSize: 'var(--onto-fs-xs)',
                  color: 'var(--onto-ink-muted)',
                  marginTop: 2,
                  fontFeatureSettings: '"tnum" 1',
                }}
              >
                {dateStr}
                {v.created_by ? ` · ${v.created_by}` : ''}
              </div>
              {v.changelog && (
                <p
                  style={{
                    fontSize: 'var(--onto-fs-xs)',
                    color: 'var(--onto-ink-muted)',
                    margin: 'var(--onto-space-1) 0 0',
                    lineHeight: 'var(--lh-tight)',
                  }}
                >
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
