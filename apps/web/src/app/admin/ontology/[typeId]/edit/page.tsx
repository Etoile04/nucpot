/**
 * Ontology Edit Page — /admin/ontology/[typeId]/edit
 *
 * Per NFM-3550 S.3.3 — edit entity_types and relation_types arrays.
 * When versionId is empty string, operates in "new" mode (F3 fix).
 */
'use client'

import { useState, useEffect, useCallback, use } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useOntologyDetail } from '@/features/ontology/hooks/use-ontology-detail'
import { useOntologyMutations } from '@/features/ontology/hooks/use-ontology-mutations'
import { ErrorPanel } from '@/features/ontology/components/error-panel'
import type { EntityType, RelationType } from '@/features/ontology/types'

interface EditPageProps {
  params: Promise<{ typeId: string }>
}

interface EntityFormRow {
  name: string
  chinese_name: string
  english_name: string
  domain: string
  description: string
}

interface RelationFormRow {
  name: string
  source_types: string
  target_types: string
  description: string
}

function entityToRow(et: EntityType): EntityFormRow {
  return {
    name: et.name,
    chinese_name: et.chinese_name ?? '',
    english_name: et.english_name ?? '',
    domain: et.domain ?? '',
    description: et.description ?? '',
  }
}

function relationToRow(rt: RelationType): RelationFormRow {
  return {
    name: rt.name,
    source_types: (rt.source_types ?? []).join(', '),
    target_types: (rt.target_types ?? []).join(', '),
    description: rt.description ?? '',
  }
}

function rowToEntity(r: EntityFormRow): EntityType {
  return {
    name: r.name,
    chinese_name: r.chinese_name || null,
    english_name: r.english_name || null,
    domain: r.domain || null,
    description: r.description || null,
    label_template: null,
    required_properties: null,
  }
}

function rowToRelation(r: RelationFormRow): RelationType {
  return {
    name: r.name,
    source_types: r.source_types ? r.source_types.split(',').map(s => s.trim()).filter(Boolean) : null,
    target_types: r.target_types ? r.target_types.split(',').map(s => s.trim()).filter(Boolean) : null,
    description: r.description || null,
    display_name: null,
    properties_schema: null,
  }
}

const EMPTY_ENTITY: EntityFormRow = { name: '', chinese_name: '', english_name: '', domain: '', description: '' }
const EMPTY_RELATION: RelationFormRow = { name: '', source_types: '', target_types: '', description: '' }

export function OntologyEditForm({ versionId }: { versionId: string }) {
  const router = useRouter()
  const isNew = versionId === ''

  const { version, entityTypes, relationTypes, loading: detailLoading, error: detailError, refetch } =
    useOntologyDetail(isNew ? null : versionId)
  const { saving, error: mutationError, createDraft, updateDraft, publishVersion } = useOntologyMutations()

  const [entities, setEntities] = useState<EntityFormRow[]>(
    isNew ? [{ ...EMPTY_ENTITY }] : [],
  )
  const [relations, setRelations] = useState<RelationFormRow[]>([])
  const [changelog, setChangelog] = useState('')
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!isNew && version && entityTypes.length > 0) {
      setEntities(entityTypes.map(entityToRow))
      setRelations(relationTypes.map(relationToRow))
    }
  }, [isNew, version, entityTypes, relationTypes])

  const handleAddEntity = useCallback(() => {
    setEntities(prev => [...prev, { ...EMPTY_ENTITY }])
  }, [])

  const handleRemoveEntity = useCallback((idx: number) => {
    setEntities(prev => prev.filter((_, i) => i !== idx))
  }, [])

  const handleEntityChange = useCallback((idx: number, field: keyof EntityFormRow, value: string) => {
    setEntities(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r))
  }, [])

  const handleAddRelation = useCallback(() => {
    setRelations(prev => [...prev, { ...EMPTY_RELATION }])
  }, [])

  const handleRemoveRelation = useCallback((idx: number) => {
    setRelations(prev => prev.filter((_, i) => i !== idx))
  }, [])

  const handleRelationChange = useCallback((idx: number, field: keyof RelationFormRow, value: string) => {
    setRelations(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r))
  }, [])

  const handleSaveDraft = useCallback(async () => {
    try {
      const ontologyData = {
        entity_types: entities.map(rowToEntity),
        relation_types: relations.map(rowToRelation),
      }
      if (isNew) {
        const created = await createDraft.mutateAsync({ changelog, ontologyData })
        setSuccessMsg('Draft saved')
        router.push('/admin/ontology/' + created.id)
      } else {
        await updateDraft.mutateAsync({ versionId, patch: { ontology_data: ontologyData, changelog } })
        setSuccessMsg('Draft saved')
      }
    } catch {
      // error surfaced via mutationError
    }
  }, [isNew, versionId, entities, relations, changelog, createDraft, updateDraft, router])

  const handlePromote = useCallback(async () => {
    if (!versionId) return
    try {
      await publishVersion.mutateAsync({ versionId, changelog })
      setSuccessMsg('Published')
      setTimeout(() => router.push('/admin/ontology/' + versionId), 2000)
    } catch {
      // error surfaced via mutationError
    }
  }, [versionId, changelog, publishVersion, router])

  if (detailLoading) {
    return (
      <div className="max-w-2xl mx-auto p-6">Loading...</div>
    )
  }

  if (detailError && !isNew) {
    return (
      <div className="max-w-2xl mx-auto p-6">
        <ErrorPanel variant="edit" message={detailError} onRetry={refetch} />
      </div>
    )
  }

  if (successMsg) {
    return (
      <div className="max-w-2xl mx-auto p-6 text-center">
        <p className="text-emerald-400 text-sm">{successMsg}</p>
        {!isNew && (
          <Link href={'/admin/ontology/' + versionId} className="text-blue-400 text-sm mt-3 inline-block">
            Go to detail
          </Link>
        )}
      </div>
    )
  }

  const backHref = isNew ? '/admin/ontology' : '/admin/ontology/' + versionId

  const inputCls = 'w-full px-3 py-2 rounded border border-gray-600 bg-gray-900 text-gray-200 text-sm outline-none focus:border-blue-500'

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <header className="mb-6">
          <Link href={backHref} className="text-gray-400 hover:text-gray-200 text-sm no-underline">
            {"< "} Back to list
          </Link>
        </header>

        <h1 className="text-2xl font-bold text-gray-100 mb-2">
          {isNew ? 'New ontology version' : `Edit v${version?.version ?? ''}`}
        </h1>
        <div className="h-px bg-gray-700 mb-8" />

        <form onSubmit={(e) => { e.preventDefault(); void handleSaveDraft() }}>
          <fieldset className="border-none p-0 mb-8">
            <legend className="text-lg font-semibold text-gray-100 mb-4 block">Entity Types</legend>
            <div className="space-y-4">
              {entities.map((entity, idx) => (
                <div key={idx} className="p-4 bg-gray-800 rounded-lg border border-gray-700">
                  <div className="flex items-center justify-between mb-3">
                    <span className="font-mono text-sm text-gray-300">Entity #{idx + 1}</span>
                    {entities.length > 1 && (
                      <button
                        type="button"
                        onClick={() => handleRemoveEntity(idx)}
                        className="text-red-400 text-sm hover:text-red-300 bg-transparent border-none cursor-pointer"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <label className="block">
                      <span className="block text-gray-300 text-sm mb-1">Type ID *</span>
                      <input
                        value={entity.name}
                        onChange={(e) => handleEntityChange(idx, 'name', e.target.value)}
                        disabled={!isNew}
                        required
                        aria-required="true"
                        placeholder="e.g. mat.zr_alloy_phase"
                        className={inputCls}
                      />
                    </label>
                    <label className="block">
                      <span className="block text-gray-300 text-sm mb-1">Domain</span>
                      <input
                        value={entity.domain}
                        onChange={(e) => handleEntityChange(idx, 'domain', e.target.value)}
                        placeholder="e.g. Nuclear cladding"
                        className={inputCls}
                      />
                    </label>
                    <label className="block">
                      <span className="block text-gray-300 text-sm mb-1">Chinese label</span>
                      <input
                        value={entity.chinese_name}
                        onChange={(e) => handleEntityChange(idx, 'chinese_name', e.target.value)}
                        className={inputCls}
                      />
                    </label>
                    <label className="block">
                      <span className="block text-gray-300 text-sm mb-1">English label</span>
                      <input
                        value={entity.english_name}
                        onChange={(e) => handleEntityChange(idx, 'english_name', e.target.value)}
                        className={inputCls}
                      />
                    </label>
                  </div>
                  <label className="block">
                    <span className="block text-gray-300 text-sm mb-1">Description</span>
                    <textarea
                      value={entity.description}
                      onChange={(e) => handleEntityChange(idx, 'description', e.target.value)}
                      rows={3}
                      className={inputCls + ' font-mono resize-y min-h-[80px]'}
                    />
                  </label>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={handleAddEntity}
              className="mt-4 px-4 py-2 rounded border border-dashed border-gray-600 text-gray-300 text-sm hover:bg-gray-800 hover:border-gray-500 transition-colors"
            >
              + Add entity type
            </button>
          </fieldset>

          <fieldset className="border-none p-0 mb-8">
            <legend className="text-lg font-semibold text-gray-100 mb-4 block">Relation Types</legend>
            {relations.length === 0 ? (
              <p className="text-gray-500 text-sm">No relation types defined.</p>
            ) : (
              <div className="space-y-4">
                {relations.map((rel, idx) => (
                  <div key={idx} className="p-4 bg-gray-800 rounded-lg border border-gray-700">
                    <div className="flex items-center justify-between mb-3">
                      <span className="font-mono text-sm text-gray-300">Relation #{idx + 1}</span>
                      <button
                        type="button"
                        onClick={() => handleRemoveRelation(idx)}
                        className="text-red-400 text-sm hover:text-red-300 bg-transparent border-none cursor-pointer"
                      >
                        Remove
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <label className="block">
                        <span className="block text-gray-300 text-sm mb-1">Relation name *</span>
                        <input
                          value={rel.name}
                          onChange={(e) => handleRelationChange(idx, 'name', e.target.value)}
                          placeholder="e.g. has_composition"
                          className={inputCls}
                        />
                      </label>
                      <div />
                      <label className="block col-span-2">
                        <span className="block text-gray-300 text-sm mb-1">Source types (comma-separated)</span>
                        <input
                          value={rel.source_types}
                          onChange={(e) => handleRelationChange(idx, 'source_types', e.target.value)}
                          placeholder="e.g. mat.zr_alloy_phase, mat.zr_alloy_component"
                          className={inputCls}
                        />
                      </label>
                      <label className="block col-span-2">
                        <span className="block text-gray-300 text-sm mb-1">Target types (comma-separated)</span>
                        <input
                          value={rel.target_types}
                          onChange={(e) => handleRelationChange(idx, 'target_types', e.target.value)}
                          placeholder="e.g. mat.property"
                          className={inputCls}
                        />
                      </label>
                      <label className="block col-span-2">
                        <span className="block text-gray-300 text-sm mb-1">Description</span>
                        <textarea
                          value={rel.description}
                          onChange={(e) => handleRelationChange(idx, 'description', e.target.value)}
                          rows={2}
                          className={inputCls + ' resize-y min-h-[60px]'}
                        />
                      </label>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <button
              type="button"
              onClick={handleAddRelation}
              className="mt-4 px-4 py-2 rounded border border-dashed border-gray-600 text-gray-300 text-sm hover:bg-gray-800 hover:border-gray-500 transition-colors"
            >
              + Add relation type
            </button>
          </fieldset>

          <fieldset className="border-none p-0 mb-8">
            <legend className="text-lg font-semibold text-gray-100 mb-4 block">Changelog</legend>
            <textarea
              value={changelog}
              onChange={(e) => setChangelog(e.target.value)}
              rows={3}
              aria-label="Changelog"
              className={inputCls + ' font-mono resize-y min-h-[80px]'}
            />
          </fieldset>

          {mutationError ? (
            <div role="alert" aria-live="polite" className="text-red-400 text-sm mb-6">
              {mutationError}
            </div>
          ) : null}

          <div className="sticky bottom-0 bg-gray-800 border-t border-gray-700 py-3 flex justify-end gap-3">
            <button
              type="submit"
              disabled={saving}
              aria-disabled={saving}
              className="px-5 py-2 rounded border border-gray-500 bg-gray-700 text-gray-200 text-sm font-medium cursor-pointer hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {saving ? 'Saving...' : 'Save draft'}
            </button>
            {!isNew && (
              <button
                type="button"
                disabled={saving}
                onClick={() => void handlePromote()}
                className="px-5 py-2 rounded bg-blue-600 border border-blue-600 text-white text-sm font-medium cursor-pointer hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {saving ? 'Publishing...' : 'Promote and publish'}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  )
}

export function EditPage({ params }: EditPageProps) {
  const { typeId } = use(params)
  return <OntologyEditForm versionId={typeId} />
}

export default EditPage
