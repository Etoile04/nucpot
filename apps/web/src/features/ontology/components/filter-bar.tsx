/**
 * FilterBar: sticky filter controls for the ontology list page.
 *
 * Per NFM-3550 §2 — surface-2 background, chips for active filters.
 * URL-synced state via searchParams.
 */
'use client'

import { useCallback } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import type { FilterStatus } from '../types'
import { STATUS_LABELS } from '../types'

interface FilterBarProps {
  readonly statusCounts?: Record<string, number>
}

const STATUS_OPTIONS: readonly FilterStatus[] = ['all', 'draft', 'published', 'deprecated'] as const

export function FilterBar({ statusCounts }: FilterBarProps) {
  const router = useRouter()
  const searchParams = useSearchParams()

  const currentStatus = (searchParams.get('status') as FilterStatus) ?? 'all'
  const currentQuery = searchParams.get('q') ?? ''

  const updateParams = useCallback(
    (patch: { status?: FilterStatus; query?: string }) => {
      const params = new URLSearchParams(searchParams.toString())
      if (patch.status && patch.status !== 'all') {
        params.set('status', patch.status)
      } else {
        params.delete('status')
      }
      if (patch.query !== undefined) {
        if (patch.query) {
          params.set('q', patch.query)
        } else {
          params.delete('q')
        }
      }
      params.delete('page')
      router.push(`/admin/ontology?${params.toString()}`)
    },
    [router, searchParams],
  )

  return (
    <div
      className="onto-filter-bar"
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 'var(--onto-space-2)',
        padding: 'var(--onto-space-3) var(--onto-space-4)',
        backgroundColor: 'var(--onto-surface-2)',
        borderRadius: 'var(--onto-radius-xs)',
        position: 'sticky' as const,
        top: 0,
        zIndex: 10,
      }}
      role="group"
      aria-label="Status filter"
    >
      <span
        style={{
          color: 'var(--onto-ink-muted)',
          fontSize: 'var(--onto-fs-sm)',
          marginRight: 'var(--onto-space-1)',
        }}
      >
        Status:
      </span>
      {STATUS_OPTIONS.map((s) => {
        const isActive = currentStatus === s
        const label = s === 'all' ? 'All' : (STATUS_LABELS[s] ?? s)
        const count = s === 'all'
          ? Object.values(statusCounts ?? {}).reduce((a, b) => a + b, 0)
          : (statusCounts ?? {})[s]
        return (
          <button
            key={s}
            aria-pressed={isActive}
            onClick={() => updateParams({ status: s })}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 'var(--onto-space-1)',
              padding: 'var(--onto-space-1) var(--onto-space-3)',
              borderRadius: 'var(--onto-radius-pill)',
              border: '1px solid',
              borderColor: isActive
                ? 'var(--onto-accent)'
                : 'var(--onto-border-soft)',
              backgroundColor: isActive
                ? 'var(--onto-accent)'
                : 'transparent',
              color: isActive
                ? 'var(--onto-ink-inverse)'
                : 'var(--onto-ink-default)',
              fontSize: 'var(--onto-fs-sm)',
              cursor: 'pointer',
              transition: `all var(--onto-dur-fast) var(--onto-ease-out)`,
              fontFamily: 'inherit',
            }}
          >
            {label}
            {count !== undefined && (
              <span
                style={{
                  fontFeatureSettings: '"tnum" 1',
                  opacity: isActive ? 0.85 : 0.6,
                  fontSize: 'var(--onto-fs-xs)',
                }}
              >
                {count}
              </span>
            )}
          </button>
        )
      })}

      <div
        style={{
          marginLeft: 'auto',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--onto-space-2)',
        }}
      >
        <input
          type="search"
          placeholder="Search ontology types..."
          value={currentQuery}
          onChange={(e) => updateParams({ query: e.target.value })}
          aria-label="Search ontology types"
          style={{
            width: 220,
            padding: 'var(--onto-space-1) var(--onto-space-3)',
            borderRadius: 'var(--onto-radius-sm)',
            border: '1px solid var(--onto-border-soft)',
            backgroundColor: 'var(--onto-surface-1)',
            color: 'var(--onto-ink-default)',
            fontSize: 'var(--onto-fs-sm)',
            fontFamily: 'inherit',
            outline: 'none',
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = 'var(--onto-border-focus)'
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = 'var(--onto-border-soft)'
          }}
        />
        {currentQuery && (
          <button
            onClick={() => updateParams({ query: '' })}
            aria-label="Clear search"
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--onto-ink-muted)',
              cursor: 'pointer',
              padding: 'var(--onto-space-1)',
              fontSize: 'var(--onto-fs-sm)',
            }}
          >
            {String.fromCharCode(10005)}
          </button>
        )}
      </div>
    </div>
  )
}
