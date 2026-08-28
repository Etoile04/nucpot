/**
 * Ontology Detail Page — /admin/ontology/[typeId]
 *
 * Displays a single version's metadata, entity types, and relation types.
 * Uses TanStack Query hooks.
 */
'use client'

import { use } from 'react'
import Link from 'next/link'
import { useOntologyDetail } from '@/features/ontology/hooks/use-ontology-detail'
import { StatusChip } from '@/features/ontology/components/status-chip'
import { ErrorPanel } from '@/features/ontology/components/error-panel'
import { RoleGate } from '@/features/ontology/components/role-gate'
import { useOntologyMutations } from '@/features/ontology/hooks/use-ontology-mutations'
import { useState, useCallback } from 'react'

export function OntologyDetailContent({ typeId }: { typeId: string }) {
  const { version, entityTypes, relationTypes, loading, error, refetch } =
    useOntologyDetail(typeId)
  const { publishVersion, deprecateVersion, saving, error: mutError } = useOntologyMutations()
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const handlePromote = useCallback(async () => {
    try {
      await publishVersion.mutateAsync({ versionId: typeId, changelog: '' })
      setActionMsg('Published successfully')
      void refetch()
    } catch {
      // surfaced via mutError
    }
  }, [typeId, publishVersion, refetch])

  const handleDeprecate = useCallback(async () => {
    try {
      await deprecateVersion.mutateAsync({ versionId: typeId })
      setActionMsg('Deprecated successfully')
      void refetch()
    } catch {
      // surfaced via mutError
    }
  }, [typeId, deprecateVersion, refetch])

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900">
        <div className="max-w-3xl mx-auto px-6 py-8">
          <div className="animate-pulse space-y-4">
            <div className="h-8 w-64 bg-gray-700 rounded" />
            <div className="h-px bg-gray-700" />
            <div className="h-32 bg-gray-700 rounded" />
          </div>
        </div>
      </div>
    )
  }

  if (error || !version) {
    return (
      <div className="min-h-screen bg-gray-900">
        <div className="max-w-3xl mx-auto px-6 py-8">
          <ErrorPanel variant="detail" message={error ?? 'Version not found'} onRetry={refetch} />
        </div>
      </div>
    )
  }

  const canEdit = version.status === 'draft'
  const canPublish = version.status === 'draft'
  const canDeprecate = version.status === 'published'

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <header className="mb-6">
          <Link href="/admin/ontology" className="text-gray-400 hover:text-gray-200 text-sm no-underline">
            ‹ Back to list
          </Link>
        </header>

        <div className="flex items-start justify-between mb-2">
          <h1 className="text-2xl font-bold">
            Version {version.version}
          </h1>
          <StatusChip status={version.status} />
        </div>

        {version.changelog && (
          <p className="text-gray-400 text-sm mb-4">{version.changelog}</p>
        )}

        <div className="h-px bg-gray-700 mb-6" />

        {/* Metadata grid */}
        <div className="grid grid-cols-3 gap-4 mb-8 text-sm">
          <div>
            <span className="block text-gray-500 text-xs uppercase tracking-wide">Created</span>
            <span className="text-gray-200">{version.created_at ? new Date(version.created_at).toLocaleDateString() : '—'}</span>
          </div>
          <div>
            <span className="block text-gray-500 text-xs uppercase tracking-wide">Updated</span>
            <span className="text-gray-200">{version.updated_at ? new Date(version.updated_at).toLocaleDateString() : '—'}</span>
          </div>
          <div>
            <span className="block text-gray-500 text-xs uppercase tracking-wide">Changelog</span>
            <span className="text-gray-200">{version.changelog ?? '—'}</span>
          </div>
        </div>

        {/* Action message */}
        {actionMsg && (
          <p className="text-emerald-400 text-sm mb-6">{actionMsg}</p>
        )}
        {mutError && (
          <p className="text-red-400 text-sm mb-6" role="alert">{mutError}</p>
        )}

        {/* Entity Types */}
        <section className="mb-8">
          <h2 className="text-lg font-semibold mb-3">
            Entity Types ({entityTypes.length})
          </h2>
          {entityTypes.length === 0 ? (
            <p className="text-gray-500 text-sm">No entity types defined.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-400 border-b border-gray-700">
                    <th className="pb-2 pr-4 font-medium">Type ID</th>
                    <th className="pb-2 pr-4 font-medium">Chinese</th>
                    <th className="pb-2 pr-4 font-medium">English</th>
                    <th className="pb-2 font-medium">Domain</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800">
                  {entityTypes.map((et) => (
                    <tr key={et.name}>
                      <td className="py-2 pr-4 font-mono text-blue-300">{et.name}</td>
                      <td className="py-2 pr-4">{et.chinese_name ?? '—'}</td>
                      <td className="py-2 pr-4">{et.english_name ?? '—'}</td>
                      <td className="py-2">{et.domain ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Relation Types */}
        <section className="mb-8">
          <h2 className="text-lg font-semibold mb-3">
            Relation Types ({relationTypes.length})
          </h2>
          {relationTypes.length === 0 ? (
            <p className="text-gray-500 text-sm">No relation types defined.</p>
          ) : (
            <div className="space-y-3">
              {relationTypes.map((rt) => (
                <div key={rt.name} className="p-3 bg-gray-800 rounded border border-gray-700">
                  <div className="font-mono text-sm text-blue-300 mb-1">{rt.name}</div>
                  {rt.description && (
                    <p className="text-gray-400 text-xs mb-2">{rt.description}</p>
                  )}
                  <div className="flex gap-4 text-xs text-gray-400">
                    <span>Source: {(rt.source_types ?? []).join(', ') || '—'}</span>
                    <span>Target: {(rt.target_types ?? []).join(', ') || '—'}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Actions */}
        <div className="h-px bg-gray-700 mb-6" />
        <RoleGate allow={['admin', 'domain_expert'] as const}>
          <div className="flex gap-3">
            {canEdit && (
              <Link
                href={'/admin/ontology/' + typeId + '/edit'}
                className="px-4 py-2 rounded border border-gray-500 bg-gray-700 text-gray-200 text-sm font-medium no-underline hover:bg-gray-600 transition-colors"
              >
                Edit draft
              </Link>
            )}
            {canPublish && (
              <button
                onClick={() => void handlePromote()}
                disabled={saving}
                className="px-4 py-2 rounded bg-blue-600 text-white text-sm font-medium border-none cursor-pointer hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {saving ? 'Publishing...' : 'Promote & publish'}
              </button>
            )}
            {canDeprecate && (
              <button
                onClick={() => void handleDeprecate()}
                disabled={saving}
                className="px-4 py-2 rounded border border-red-700 bg-red-900/30 text-red-300 text-sm font-medium cursor-pointer hover:bg-red-900/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {saving ? 'Deprecating...' : 'Deprecate'}
              </button>
            )}
          </div>
        </RoleGate>
      </div>
    </div>
  )
}

export default function OntologyDetailPage({ params }: { params: Promise<{ typeId: string }> }) {
  const { typeId } = use(params)
  return <OntologyDetailContent typeId={typeId} />
}
