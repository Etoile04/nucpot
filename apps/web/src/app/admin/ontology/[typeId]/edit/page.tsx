/**
 * Ontology Edit Page — /admin/ontology/[typeId]/edit
 * Ontology New Page — /admin/ontology/new
 *
 * Per NFM-3550 §3.3 — form with identity, definition, relations,
 * and promote workflow.
 */
'use client'

import { useState, useEffect, useCallback, use } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useOntologyDetail } from '@/features/ontology/hooks/use-ontology-detail'
import { useOntologyMutations } from '@/features/ontology/hooks/use-ontology-mutations'
import { ErrorPanel } from '@/features/ontology/components/error-panel'

interface EditPageProps {
  params: Promise<{ typeId: string }>
}

function OntologyEditForm({ versionId }: { versionId: string | null }) {
  const router = useRouter()
  const { version, entityTypes, loading: detailLoading, error: detailError, refetch } =
    versionId ? useOntologyDetail(versionId) : { version: null, entityTypes: [], loading: false, error: null, refetch: async () => {} }
  const { saving, error: mutationError, createDraft, updateDraft, publishVersion } = useOntologyMutations()

  const [name, setName] = useState('')
  const [chineseName, setChineseName] = useState('')
  const [englishName, setEnglishName] = useState('')
  const [domain, setDomain] = useState('')
  const [definition, setDefinition] = useState('')
  const [changelog] = useState('')
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  // Populate form from existing version
  useEffect(() => {
    if (version && entityTypes.length > 0) {
      const first = entityTypes[0]!
      setName(first.name)
      setChineseName(first.chinese_name ?? '')
      setEnglishName(first.english_name ?? '')
      setDomain(first.domain ?? '')
      setDefinition(first.description ?? '')
    }
  }, [version, entityTypes])

  const isNew = !versionId

  const handleSaveDraft = useCallback(async () => {
    try {
      const ontologyData = {
        entity_types: [{
          name: name || 'untyped',
          chinese_name: chineseName || null,
          english_name: englishName || null,
          domain: domain || null,
          description: definition || null,
          label_template: null,
          required_properties: null,
        }],
        relation_types: [],
      }
      if (isNew) {
        const created = await createDraft(changelog, ontologyData)
        setSuccessMsg('Draft saved · ' + new Date().toLocaleTimeString())
        router.push('/admin/ontology/' + created.id)
      } else if (versionId) {
        await updateDraft(versionId, { ontology_data: ontologyData, changelog })
        setSuccessMsg('Draft saved · ' + new Date().toLocaleTimeString())
      }
    } catch {
      // error is set in the hook
    }
  }, [isNew, versionId, name, chineseName, englishName, domain, definition, changelog, createDraft, updateDraft, router])

  const handlePromote = useCallback(async () => {
    if (!versionId) return
    try {
      await publishVersion(versionId, changelog)
      setSuccessMsg('Published · redirecting to detail...')
      setTimeout(() => router.push('/admin/ontology/' + versionId), 2000)
    } catch {
      // error is set in the hook
    }
  }, [versionId, changelog, publishVersion, router])

  const isValid = name.length > 0 && /^[a-z][a-z0-9_.]{1,63}$/.test(name)

  if (detailLoading) {
    return <div style={{ maxWidth: 'var(--onto-container-narrow)', margin: '0 auto', padding: 'var(--onto-space-6)' }}>Loading...</div>
  }

  if (detailError && !isNew) {
    return (
      <div style={{ maxWidth: 'var(--onto-container-narrow)', margin: '0 auto', padding: 'var(--onto-space-6)' }}>
        <ErrorPanel variant="edit" message={detailError} onRetry={refetch} />
      </div>
    )
  }

  if (successMsg) {
    return (
      <div style={{ maxWidth: 'var(--onto-container-narrow)', margin: '0 auto', padding: 'var(--onto-space-6)', textAlign: 'center' }}>
        <p style={{ color: 'var(--onto-accent-success)', fontSize: 'var(--onto-fs-body)' }}>{successMsg}</p>
        {!isNew && (
          <Link href={'/admin/ontology/' + versionId} style={{ color: 'var(--onto-accent)', fontSize: 'var(--onto-fs-sm)', marginTop: 'var(--onto-space-3)', display: 'inline-block' }}>
            Go to detail
          </Link>
        )}
      </div>
    )
  }

  return (
    <div className="onto-animate" style={{ maxWidth: 'var(--onto-container-narrow)', margin: '0 auto', padding: 'var(--onto-space-5) var(--onto-space-6)', backgroundColor: 'var(--onto-surface-0)', minHeight: '100vh' }}>
      <header style={{ marginBottom: 'var(--onto-space-4)' }}>
        <Link href={isNew ? '/admin/ontology' : '/admin/ontology/' + versionId} style={{ color: 'var(--onto-ink-muted)', textDecoration: 'none', fontSize: 'var(--onto-fs-sm)' }}>
          {'‹'} Back to detail
        </Link>
      </header>

      <h1 style={{ fontFamily: 'var(--onto-font-display)', fontSize: 'var(--onto-fs-h1)', color: 'var(--onto-ink-strong)', margin: '0 0 var(--onto-space-2)' }}>
        {isNew ? 'New ontology type' : 'Edit v' + (version?.version ?? '')}
      </h1>
      <div style={{ height: 1, backgroundColor: 'var(--onto-border-soft)', marginBottom: 'var(--onto-space-5)' }} />

      <form onSubmit={(e) => { e.preventDefault(); void handleSaveDraft() }}>
        {/* Identity section */}
        <fieldset style={{ border: 'none', padding: 0, marginBottom: 'var(--onto-space-5)' }}>
          <legend style={{ fontFamily: 'var(--onto-font-display)', fontSize: 'var(--onto-fs-h2)', color: 'var(--onto-ink-strong)', marginBottom: 'var(--onto-space-3)' }}>Identity</legend>

          <div style={{ marginBottom: 'var(--onto-space-4)' }}>
            <label htmlFor="type-id" style={labelStyle}>Type ID <span aria-hidden="true">*</span></label>
            <input id="type-id" value={name} onChange={(e) => setName(e.target.value)} disabled={!isNew} required aria-required="true" style={inputStyle} placeholder="e.g. mat.zr_alloy_phase" />
            {!isValid && name.length > 0 && (
              <p id="type-id-error" style={errorTextStyle} role="alert">Type ID: lowercase letters, digits, dots, underscores (1-64 chars)</p>
            )}
          </div>

          <div style={{ marginBottom: 'var(--onto-space-4)' }}>
            <label htmlFor="chinese-name" style={labelStyle}>Chinese label <span aria-hidden="true">*</span></label>
            <input id="chinese-name" value={chineseName} onChange={(e) => setChineseName(e.target.value)} required aria-required="true" style={inputStyle} />
          </div>

          <div style={{ marginBottom: 'var(--onto-space-4)' }}>
            <label htmlFor="english-name" style={labelStyle}>English label <span aria-hidden="true">*</span></label>
            <input id="english-name" value={englishName} onChange={(e) => setEnglishName(e.target.value)} required aria-required="true" style={inputStyle} />
          </div>

          <div style={{ marginBottom: 'var(--onto-space-4)' }}>
            <label htmlFor="domain" style={labelStyle}>Domain</label>
            <input id="domain" value={domain} onChange={(e) => setDomain(e.target.value)} style={inputStyle} placeholder="e.g. Nuclear cladding" />
          </div>
        </fieldset>

        {/* Definition section */}
        <fieldset style={{ border: 'none', padding: 0, marginBottom: 'var(--onto-space-5)' }}>
          <legend style={{ fontFamily: 'var(--onto-font-display)', fontSize: 'var(--onto-fs-h2)', color: 'var(--onto-ink-strong)', marginBottom: 'var(--onto-space-3)' }}>Definition</legend>
          <textarea id="definition" value={definition} onChange={(e) => setDefinition(e.target.value)} rows={6} aria-label="Definition" style={{ ...inputStyle, fontFamily: 'var(--onto-font-mono)', resize: 'vertical', minHeight: 120 }} />
        </fieldset>

        {/* Error display */}
        {mutationError && (
          <div role="alert" aria-live="polite" style={{ color: 'var(--onto-accent-danger)', fontSize: 'var(--onto-fs-sm)', marginBottom: 'var(--onto-space-3)' }}>
            {mutationError}
          </div>
        )}

        {/* Sticky bottom action bar */}
        <div style={{ position: 'sticky' as const, bottom: 0, backgroundColor: 'var(--onto-surface-1)', borderTop: '1px solid var(--onto-border-soft)', padding: 'var(--onto-space-3) 0', display: 'flex', justifyContent: 'flex-end', gap: 'var(--onto-space-3)', marginTop: 'var(--onto-space-5)' }}>
          <button type="submit" disabled={saving || !isValid} aria-disabled={!isValid} style={{ ...btnStyle, backgroundColor: 'var(--onto-surface-2)', borderColor: 'var(--onto-border-strong)' }}>
            {saving ? 'Saving...' : 'Save draft'}
          </button>
          {!isNew && versionId && (
            <button type="button" disabled={saving} onClick={() => void handlePromote()} style={{ ...btnStyle, backgroundColor: 'var(--onto-accent)', borderColor: 'var(--onto-accent)', color: 'var(--onto-ink-inverse)' }}>
              {saving ? 'Publishing...' : 'Promote & publish'}
            </button>
          )}
        </div>
      </form>
    </div>
  )
}

const labelStyle: React.CSSProperties = {
  display: 'block',
  color: 'var(--onto-ink-default)',
  fontSize: 'var(--onto-fs-sm)',
  marginBottom: 'var(--onto-space-1)',
  fontWeight: 500,
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: 'var(--onto-space-2) var(--onto-space-3)',
  borderRadius: 'var(--onto-radius-sm)',
  border: '1px solid var(--onto-border-soft)',
  backgroundColor: 'var(--onto-surface-1)',
  color: 'var(--onto-ink-default)',
  fontSize: 'var(--onto-fs-sm)',
  fontFamily: 'inherit',
  outline: 'none',
  boxSizing: 'border-box',
}

const errorTextStyle: React.CSSProperties = {
  color: 'var(--onto-accent-danger)',
  fontSize: 'var(--onto-fs-xs)',
  marginTop: 'var(--onto-space-1)',
  marginBottom: 0,
}

const btnStyle: React.CSSProperties = {
  padding: 'var(--onto-space-2) var(--onto-space-5)',
  borderRadius: 'var(--onto-radius-sm)',
  border: '1px solid',
  fontSize: 'var(--onto-fs-sm)',
  fontFamily: 'inherit',
  cursor: 'pointer',
  fontWeight: 500,
}

export function EditPage({ params }: EditPageProps) {
  const { typeId } = use(params)
  return <OntologyEditForm versionId={typeId} />
}

export default EditPage
