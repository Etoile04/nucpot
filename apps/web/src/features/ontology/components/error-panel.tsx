/**
 * ErrorPanel: surface-1 panel with retry and copy-request-id.
 *
 * Per NFM-3550 §4 — domain-aware copy, bilingual.
 */
'use client'

import { useCallback, useState } from 'react'

interface ErrorPanelProps {
  readonly httpStatus?: number
  readonly requestId?: string
  readonly message?: string
  readonly onRetry?: () => void
  readonly variant?: 'list' | 'detail' | 'edit'
}

const COPY: Record<string, { zh: string; en: string }> = {
  list: {
    zh: '无法加载本体列表',
    en: "Couldn't load the ontology list",
  },
  detail: {
    zh: '加载失败',
    en: 'Failed to load',
  },
  edit: {
    zh: '保存失败',
    en: 'Failed to save',
  },
}

export function ErrorPanel({
  httpStatus,
  requestId,
  message,
  onRetry,
  variant = 'list',
}: ErrorPanelProps) {
  const [copied, setCopied] = useState(false)
  const labels = COPY[variant] ?? COPY.list
  const statusSuffix = httpStatus ? ` (${httpStatus})` : ''
  const detail = message ?? `${labels.en}${statusSuffix}.`

  const copyId = useCallback(() => {
    if (!requestId) return
    void navigator.clipboard.writeText(requestId).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [requestId])

  return (
    <div
      role="alert"
      aria-live="assertive"
      style={{
        padding: 'var(--onto-space-5) var(--onto-space-6)',
        backgroundColor: 'var(--onto-surface-1)',
        borderRadius: 'var(--onto-radius-md)',
        border: '1px solid var(--onto-accent-danger)',
        textAlign: 'center',
        maxWidth: 'var(--onto-container-narrow)',
        margin: 'var(--onto-space-6) auto',
      }}
    >
      <svg
        width={48}
        height={48}
        viewBox="0 0 48 48"
        fill="none"
        aria-hidden="true"
        style={{ marginBottom: 'var(--onto-space-3)' }}
      >
        <rect x="4" y="4" width="40" height="40" rx="4" stroke="var(--onto-accent-danger)" strokeWidth="2" />
        <path d="M16 20h16M16 28h10" stroke="var(--onto-accent-danger)" strokeWidth="2" strokeLinecap="round" />
        <path d="M24 4v8" stroke="var(--onto-accent-danger)" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <p style={{ color: 'var(--onto-ink-default)', fontSize: 'var(--onto-fs-body)', margin: 0 }}>
        {detail}
      </p>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 'var(--onto-space-3)', marginTop: 'var(--onto-space-4)' }}>
        {onRetry && (
          <button
            onClick={onRetry}
            style={{
              padding: 'var(--onto-space-2) var(--onto-space-4)',
              borderRadius: 'var(--onto-radius-sm)',
              border: '1px solid var(--onto-border-strong)',
              backgroundColor: 'var(--onto-surface-2)',
              color: 'var(--onto-ink-default)',
              cursor: 'pointer',
              fontSize: 'var(--onto-fs-sm)',
              fontFamily: 'inherit',
            }}
          >
            Retry
          </button>
        )}
        {requestId && (
          <button
            onClick={copyId}
            style={{
              padding: 'var(--onto-space-2) var(--onto-space-4)',
              borderRadius: 'var(--onto-radius-sm)',
              border: '1px solid var(--onto-border-soft)',
              background: 'none',
              color: 'var(--onto-ink-muted)',
              cursor: 'pointer',
              fontSize: 'var(--onto-fs-sm)',
              fontFamily: 'inherit',
            }}
          >
            {copied ? 'Copied' : 'Copy request ID'}
          </button>
        )}
      </div>
    </div>
  )
}
