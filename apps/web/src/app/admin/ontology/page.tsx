/**
 * Ontology List Page — /admin/ontology
 *
 * Per NFM-3550 §3.1 — DataTable + FilterBar + StatusChip.
 * Reads from GET /api/v1/ontology/versions.
 */
'use client'

import { Suspense, useCallback, useMemo } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { useOntologyVersions } from '@/features/ontology/hooks/use-ontology-versions'
import { FilterBar } from '@/features/ontology/components/filter-bar'
import { StatusChip } from '@/features/ontology/components/status-chip'
import { SkeletonTable } from '@/features/ontology/components/skeleton-table'
import { ErrorPanel } from '@/features/ontology/components/error-panel'
import { EmptyState } from '@/features/ontology/components/empty-state'
import { RoleGate } from '@/features/ontology/components/role-gate'
import type { OntologyVersionStatus } from '@/features/ontology/types'
import { STATUS_LABELS } from '@/features/ontology/types'

const PER_PAGE = 50

function OntologyListInner() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const status = (searchParams.get('status') as OntologyVersionStatus | 'all') ?? 'all'
  const page = Math.max(1, Number(searchParams.get('page')) || 1)
  const query = searchParams.get('q') ?? ''

  const { versions, total, pages, loading, error, refetch } =
    useOntologyVersions(status === 'all' ? undefined : status, page)

  // Build status counts from versions (approximate — real counts need a backend endpoint)
  const statusCounts = useMemo(() => {
    // Placeholder — in production this comes from a dedicated count endpoint
    return undefined
  }, [])

  // Filter versions by query (client-side, since the API doesn't support search yet)
  const filteredVersions = useMemo(() => {
    if (!query) return versions
    const q = query.toLowerCase()
    return versions.filter((v) =>
      v.version.toLowerCase().includes(q) ||
      (v.changelog ?? '').toLowerCase().includes(q) ||
      (v.created_by ?? '').toLowerCase().includes(q),
    )
  }, [versions, query])

  const handleRowClick = useCallback(
    (id: string) => {
      router.push(`/admin/ontology/${id}`)
    },
    [router],
  )

  return (
    <div
      className="onto-animate"
      style={{
        maxWidth: 'var(--onto-container-wide)',
        margin: '0 auto',
        padding: 'var(--onto-space-5) var(--onto-space-6)',
        minHeight: '100vh',
        backgroundColor: 'var(--onto-surface-0)',
      }}
    >
      {/* Skip link */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:px-3 focus:py-1"
        style={{
          backgroundColor: 'var(--onto-accent)',
          color: 'var(--onto-ink-inverse)',
          borderRadius: 'var(--onto-radius-sm)',
          textDecoration: 'none',
        }}
      >
        Skip to main content
      </a>

      {/* Page header */
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 'var(--onto-space-4)',
        }}
      >
        <div>
          <h1
            style={{
              fontFamily: 'var(--onto-font-display)',
              fontSize: 'var(--onto-fs-display)',
              color: 'var(--onto-ink-strong)',
              margin: 0,
              letterSpacing: 'var(--onto-tracking-display)',
            }}
          >
            Admin · Ontology Management
          </h1>
          <p
            style={{
              fontSize: 'var(--onto-fs-sm)',
              color: 'var(--onto-ink-muted)',
              marginTop: 'var(--onto-space-1)',
              margin: 0,
            }}
          >
            {total} versions across all domains
          </p>
        </div>
        <RoleGate allow={['curator', 'admin']} mode='disable'>
          <Link
            href="/admin/ontology/new"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 'var(--onto-space-2)',
              padding: 'var(--onto-space-2) var(--onto-space-4)',
              borderRadius: 'var(--onto-radius-sm)',
              backgroundColor: 'var(--onto-accent)',
              color: 'var(--onto-ink-inverse)',
              fontSize: 'var(--onto-fs-sm)',
              textDecoration: 'none',
              fontWeight: 500,
              fontFamily: 'inherit',
            }}
          >
            + New ontology type
          </Link>
        </RoleGate>
      </header>

      {/* Filter bar */
      <FilterBar statusCounts={statusCounts} />

      {/* Main content */
      <main id="main">
        {loading && <SkeletonTable rows={8} />}

        {!loading && error && (
          <ErrorPanel variant="list" httpStatus={undefined} message={error} onRetry={refetch} />
        )}

        {!loading && !error && filteredVersions.length === 0 && (
          <EmptyState
            variant={query ? 'filtered' : 'no-data'}
            onClear={query ? () => router.push('/admin/ontology') : undefined}
          />
        )}

        {!loading && !error && filteredVersions.length > 0 && (
          <>
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                marginTop: 'var(--onto-space-3)',
              }}
              role="grid"
            >
              <caption className="sr-only">
                Ontology versions — {filteredVersions.length} of {total} results
              </caption>
              <thead>
                <tr
                  style={{
                    borderBottom: '2px solid var(--onto-border-strong)',
                    textAlign: 'left',
                  }}
                >
                  <th
                    scope="col"
                    style={{
                      padding: 'var(--onto-space-2) var(--onto-space-3)',
                      color: 'var(--onto-ink-muted)',
                      fontSize: 'var(--onto-fs-xs)',
                      fontWeight: 500,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                    }}
                  >
                    Version
                  </th>
                  <th
                    scope="col"
                    style={{
                      padding: 'var(--onto-space-2) var(--onto-space-3)',
                      color: 'var(--onto-ink-muted)',
                      fontSize: 'var(--onto-fs-xs)',
                      fontWeight: 500,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                    }}
                  >
                    Status
                  </th>
                  <th
                    scope="col"
                    style={{
                      padding: 'var(--onto-space-2) var(--onto-space-3)',
                      color: 'var(--onto-ink-muted)',
                      fontSize: 'var(--onto-fs-xs)',
                      fontWeight: 500,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                    }}
                  >
                    Date
                  </th>
                  <th
                    scope="col"
                    style={{
                      padding: 'var(--onto-space-2) var(--onto-space-3)',
                      color: 'var(--onto-ink-muted)',
                      fontSize: 'var(--onto-fs-xs)',
                      fontWeight: 500,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                    }}
                  >
                    Author
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredVersions.map((v, idx) => (
                  <tr
                    key={v.id}
                    role="row"
                    aria-selected={idx === 0}
                    tabIndex={0}
                    onClick={() => handleRowClick(v.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleRowClick(v.id)
                    }}
                    style={{
                      borderBottom: '1px solid var(--onto-border-soft)',
                      cursor: 'pointer',
                      transition: `background var(--onto-dur-fast) var(--onto-ease-out)`,
                      borderLeft: idx === 0
                        ? '2px solid var(--onto-accent)'
                        : '2px solid transparent',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.backgroundColor = 'var(--onto-surface-3)'
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.backgroundColor = 'transparent'
                    }}
                  >
                    <td
                      style={{
                        padding: 'var(--onto-space-2) var(--onto-space-3)',
                        color: 'var(--onto-ink-default)',
                        fontSize: 'var(--onto-fs-sm)',
                        fontFeatureSettings: '"tnum" 1',
                        fontFamily: 'var(--onto-font-mono)',
                      }}
                    >
                      v{v.version}
                    </td>
                    <td style={{ padding: 'var(--onto-space-2) var(--onto-space-3)' }}>
                      <StatusChip status={v.status} />
                    </td>
                    <td
                      style={{
                        padding: 'var(--onto-space-2) var(--onto-space-3)',
                        color: 'var(--onto-ink-muted)',
                        fontSize: 'var(--onto-fs-sm)',
                        fontFeatureSettings: '"tnum" 1',
                      }}
                    >
                      {new Date(v.created_at).toLocaleDateString('zh-CN')}
                    </td>
                    <td
                      style={{
                        padding: 'var(--onto-space-2) var(--onto-space-3)',
                        color: 'var(--onto-ink-default)',
                        fontSize: 'var(--onto-fs-sm)',
                      }}
                    >
                      {v.created_by ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Pagination */
            {pages > 1 && (
              <nav
                style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  alignItems: 'center',
                  gap: 'var(--onto-space-3)',
                  marginTop: 'var(--onto-space-4)',
                  fontSize: 'var(--onto-fs-sm)',
                  color: 'var(--onto-ink-muted)',
                }}
                aria-label="Pagination"
              >
                <span style={{ fontFeatureSettings: '"tnum" 1' }}>
                  {total} results
                </span>
                <div style={{ display: 'flex', gap: 'var(--onto-space-1)' }}>
                  {Array.from({ length: Math.min(pages, 7) }, (_, i) => {
                    let pageNum: number
                    if (pages <= 7) {
                      pageNum = i + 1
                    } else if (page <= 4) {
                      pageNum = i + 1
                    } else if (page >= pages - 3) {
                      pageNum = pages - 6 + i
                    } else {
                      pageNum = page - 3 + i
                    }
                    const params = new URLSearchParams(searchParams.toString())
                    params.set('page', String(pageNum))
                    const href = `/admin/ontology?${params.toString()}`
                    return (
                      <Link
                        key={pageNum}
                        href={href}
                        aria-current={pageNum === page ? 'page' : undefined}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: 32,
                          height: 32,
                          borderRadius: 'var(--onto-radius-sm)',
                          backgroundColor:
                            pageNum === page
                              ? 'var(--onto-accent)'
                              : 'transparent',
                          color:
                            pageNum === page
                              ? 'var(--onto-ink-inverse)'
                              : 'var(--onto-ink-default)',
                          fontSize: 'var(--onto-fs-sm)',
                          fontFeatureSettings: '"tnum" 1',
                          textDecoration: 'none',
                          fontFamily: 'inherit',
                        }}
                      >
                        {pageNum}
                      </Link>
                    )
                  })}
                </div>
              </nav>
            )}
          </>
        )}
      </main>
    </div>
  )
}

export default function OntologyListPage() {
  return (
    <Suspense>
      <OntologyListInner />
    </Suspense>
  )
}
