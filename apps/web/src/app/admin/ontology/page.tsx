/**
 * Ontology List Page — /admin/ontology
 *
 * Paginated version list with status filter. Uses TanStack Query hooks.
 */
'use client'

import { useState, useCallback } from 'react'
import Link from 'next/link'
import { useOntologyVersions } from '@/features/ontology/hooks/use-ontology-versions'
import { FilterBar } from '@/features/ontology/components/filter-bar'
import { VersionLane } from '@/features/ontology/components/version-lane'
import { SkeletonTable } from '@/features/ontology/components/skeleton-table'
import { EmptyState } from '@/features/ontology/components/empty-state'
import { ErrorPanel } from '@/features/ontology/components/error-panel'
import { RoleGate } from '@/features/ontology/components/role-gate'
import type { OntologyVersionStatus } from '@/features/ontology/types'

export default function OntologyListPage() {
  const [status, setStatus] = useState<OntologyVersionStatus | 'all'>('all')
  const [page, setPage] = useState(1)
  const { versions, total, pages, loading, error, refetch } = useOntologyVersions(status, page)

  const handleFilterChange = useCallback((next: OntologyVersionStatus | 'all') => {
    setStatus(next)
    setPage(1)
  }, [])

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <header className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Ontology Versions</h1>
          <RoleGate allow={["admin", "domain_expert"] as const}>
            <Link
              href="/admin/ontology/new"
              className="px-4 py-2 rounded bg-blue-600 text-white text-sm font-medium no-underline hover:bg-blue-500 transition-colors"
            >
              + New version
            </Link>
          </RoleGate>
        </header>

        <div className="h-px bg-gray-700 mb-6" />

        <FilterBar current={status} onChange={handleFilterChange} />

        {error && (
          <div className="mb-6">
            <ErrorPanel variant="list" message={error} onRetry={refetch} />
          </div>
        )}

        {loading && (
          <SkeletonTable rows={5} />
        )}

        {!loading && !error && versions.length === 0 && (
          <EmptyState title="No ontology versions found" action={<Link href="/admin/ontology/new">Create one</Link>} />
        )}

        {!loading && !error && versions.length > 0 && (
          <>
            <VersionLane versions={versions} onSelect={(id) => { window.location.href = '/admin/ontology/' + id }} />

            {/* Pagination */}
            {pages > 1 && (
              <nav className="flex items-center justify-between mt-8" aria-label="Pagination">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                  className="px-3 py-2 rounded border border-gray-600 bg-gray-800 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {"< "} Previous
                </button>
                <span className="text-gray-400 text-sm">
                  Page {page} of {pages} · {total} total
                </span>
                <button
                  disabled={page >= pages}
                  onClick={() => setPage((p) => p + 1)}
                  className="px-3 py-2 rounded border border-gray-600 bg-gray-800 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Next {"->"}
                </button>
              </nav>
            )}
          </>
        )}
      </div>
    </div>
  )
}
