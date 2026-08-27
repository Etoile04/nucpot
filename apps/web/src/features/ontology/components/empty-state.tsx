/**
 * EmptyState: bespoke empty/illustration placeholder.
 * Per NFM-3550 §4 — domain-aware bilingual copy, no stock graphics.
 */
'use client'

interface EmptyStateProps {
  readonly title: string
  readonly description?: string
  readonly action?: React.ReactNode
  }

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--onto-space-8) var(--onto-space-6)',
        textAlign: 'center',
      }}
    >
      <svg
        width="64"
        height="64"
        viewBox="0 0 64 64"
        fill="none"
        aria-hidden="true"
        style={{ marginBottom: 'var(--onto-space-4)' }}
      >
        <rect x="8" y="12" width="48" height="40" rx="4" stroke="var(--onto-ink-muted)" strokeWidth="2" />
        <line x1="16" y1="24" x2="48" y2="24" stroke="var(--onto-ink-muted)" strokeWidth="1.5" strokeDasharray="4 4" />
        <line x1="16" y1="32" x2="48" y2="32" stroke="var(--onto-ink-muted)" strokeWidth="1.5" strokeDasharray="4 4" />
        <line x1="16" y1="40" x2="48" y2="40" stroke="var(--onto-ink-muted)" strokeWidth="1.5" strokeDasharray="4 4" />
      </svg>
      <p style={{
        color: 'var(--onto-ink-default)',
        fontSize: 'var(--onto-fs-h3)',
        fontWeight: 600,
        fontFamily: 'var(--onto-font-display)',
        margin: '0',
      }}>
        {title}
      </p>
      {description && (
        <p style={{
          color: 'var(--onto-ink-muted)',
          fontSize: 'var(--onto-fs-body)',
          margin: 'var(--onto-space-1) 0 0',
          maxWidth: 400,
        }}>
          {description}
        </p>
      )}
      {action && (
        <span style={{
          display: 'inline-block',
          marginTop: 'var(--onto-space-3)',
        }}>
          {action}
        </span>
      )}
    </div>
  )
}
