/**
 * Ontology Detail Page — /admin/ontology/[typeId]
 *
 * Per NFM-3550 §3.2 — four vertical lanes:
 * Identity, Version, Relations, Audit.
 */
'use client'

import { use } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useOntologyDetail } from '@/features/ontology/hooks/use-ontology-detail'
import { VersionLane } from '@/features/ontology/components/version-lane'
import { SkeletonTable } from '@/features/ontology/components/skeleton-table'
import { ErrorPanel } from '@/features/ontology/components/error-panel'
import { RoleGate } from '@/features/ontology/components/role-gate'

const LANE_LABELS = ['Identity', 'Version', 'Relations', 'Audit'] as const

type Lane = (typeof LANE_LABELS)[number]

const TH_STYLE: React.CSSProperties = {
  padding: 'var(--onto-space-2) var(--onto-space-3)',
  color: 'var(--onto-ink-muted)',
  fontSize: 'var(--onto-fs-xs)',
  fontWeight: 500,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  textAlign: 'left' as const,
}

const TD_STYLE: React.CSSProperties = {
  padding: 'var(--onto-space-2) var(--onto-space-3)',
  color: 'var(--onto-ink-default)',
  fontSize: 'var(--onto-fs-sm)',
  verticalAlign: 'top' as const,
}

interface DetailPageProps {
  params: Promise<{ typeId: string }>
}

export default function OntologyDetailPage({ params }: DetailPageProps) {
  const { typeId } = use(params)
  const router = useRouter()
  const { version, entityTypes, relationTypes, loading, error, refetch } =
    useOntologyDetail(typeId)

  if (loading) {
    return (
      <div style={{ maxWidth: 'var(--onto-container-wide)', margin: '0 auto', padding: 'var(--onto-space-5) var(--onto-space-6)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: 'var(--onto-space-4)' }}>
          {LANE_LABELS.map((label) => (
            <div key={label} style={{ gridColumn: '1 / -1' }}>
              <div style={{ height: 12, width: 80, backgroundColor: 'var(--onto-surface-inset)', borderRadius: 'var(--onto-radius-xs)', marginBottom: 'var(--onto-space-3)' }} />
              <SkeletonTable rows={3} />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (error || !version) {
    return (
      <div style={{ maxWidth: 'var(--onto-container-wide)', margin: '0 auto', padding: 'var(--onto-space-5) var(--onto-space-6)' }}>
        <ErrorPanel variant="detail" message={error ?? 'Version ' + typeId + ' not found'} onRetry={refetch} />
        <Link href="/admin/ontology" style={{ color: 'var(--onto-accent)', fontSize: 'var(--onto-fs-sm)' }}>
          {'←'} Back to ontology list
        </Link>
      </div>
    )
  }

  return (
    <div className="onto-animate" style={{ maxWidth: 'var(--onto-container-wide)', margin: '0 auto', padding: 'var(--onto-space-5) var(--onto-space-6)', backgroundColor: 'var(--onto-surface-0)', minHeight: '100vh' }}>
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--onto-space-4)' }}>
        <Link href="/admin/ontology" style={{ color: 'var(--onto-ink-muted)', textDecoration: 'none', fontSize: 'var(--onto-fs-body)' }}>
          {'‹'} Ontology management
        </Link>
        <div style={{ display: 'flex', gap: 'var(--onto-space-3)' }}>
          <RoleGate allow={['curator', 'admin']} mode="disable">
            <Link href={'/admin/ontology/' + typeId + '/edit'} style={{ padding: 'var(--onto-space-2) var(--onto-space-4)', borderRadius: 'var(--onto-radius-sm)', border: '1px solid var(--onto-border-strong)', backgroundColor: 'var(--onto-surface-2)', color: 'var(--onto-ink-default)', fontSize: 'var(--onto-fs-sm)', textDecoration: 'none', fontFamily: 'inherit' }}>
              Edit
            </Link>
          </RoleGate>
        </div>
      </header>

      <div style={{ marginBottom: 'var(--onto-space-6)' }}>
        <h1 style={{ fontFamily: 'var(--onto-font-display)', fontSize: 'var(--onto-fs-h1)', color: 'var(--onto-ink-strong)', margin: 0 }}>
          Ontology Version v{version.version}
        </h1>
        <p style={{ color: 'var(--onto-ink-muted)', fontSize: 'var(--onto-fs-sm)', margin: 'var(--onto-space-1) 0 0' }}>
          Status: {version.status} · {new Date(version.created_at).toLocaleDateString('zh-CN')}
          {version.created_by ? ' · Curator: ' + version.created_by : ''}
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: 'var(--onto-space-5)' }}>
        <nav style={{ position: 'sticky' as const, top: 'var(--onto-space-5)', alignSelf: 'start' }} aria-label="Section navigation">
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, position: 'relative' }}>
            <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, backgroundColor: 'var(--onto-border-soft)' }} aria-hidden="true" />
            {LANE_LABELS.map((label, idx) => (
              <li key={label}>
                <a href={'#lane-' + label.toLowerCase()} style={{ display: 'block', padding: 'var(--onto-space-2) var(--onto-space-3)', paddingLeft: 'var(--onto-space-4)', color: idx === 0 ? 'var(--onto-accent)' : 'var(--onto-ink-muted)', fontSize: 'var(--onto-fs-sm)', textDecoration: 'none', borderLeft: idx === 0 ? '2px solid var(--onto-accent)' : '2px solid transparent', marginLeft: -2 }}>
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--onto-space-6)' }}>
          <section id="lane-identity" aria-labelledby="lane-identity-heading">
            <h2 id="lane-identity-heading" style={{ fontFamily: 'var(--onto-font-display)', fontSize: 'var(--onto-fs-h2)', color: 'var(--onto-ink-strong)', marginBottom: 'var(--onto-space-4)' }}>Identity</h2>
            <div style={{ backgroundColor: 'var(--onto-surface-1)', borderRadius: 'var(--onto-radius-md)', padding: 'var(--onto-space-4)' }}>
              {entityTypes.length === 0 ? (
                <p style={{ color: 'var(--onto-ink-muted)', fontSize: 'var(--onto-fs-sm)', margin: 0 }}>No entity types in this version.</p>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ borderBottom: '1px solid var(--onto-border-soft)' }}>
                    <th scope="col" style={TH_STYLE}>Type ID</th>
                    <th scope="col" style={TH_STYLE}>Display Name</th>
                    <th scope="col" style={TH_STYLE}>Domain</th>
                    <th scope="col" style={TH_STYLE}>Description</th>
                  </tr></thead>
                  <tbody>{entityTypes.map((et) => (
                    <tr key={et.name} style={{ borderBottom: '1px solid var(--onto-border-soft)' }}>
                      <td style={{ ...TD_STYLE, fontFamily: 'var(--onto-font-mono)', fontFeatureSettings: '"tnum" 1', fontSize: 'var(--onto-fs-xs)' }}>{et.name}</td>
                      <td style={TD_STYLE}>{et.display_name ?? et.chinese_name ?? et.english_name ?? et.name}</td>
                      <td style={TD_STYLE}>{et.domain ?? '—'}</td>
                      <td style={{ ...TD_STYLE, maxWidth: 300 }}><span style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{et.description ?? '—'}</span></td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
          </section>

          <section id="lane-version" aria-labelledby="lane-version-heading">
            <h2 id="lane-version-heading" style={{ fontFamily: 'var(--onto-font-display)', fontSize: 'var(--onto-fs-h2)', color: 'var(--onto-ink-strong)', marginBottom: 'var(--onto-space-4)' }}>Versions</h2>
            <VersionLane versions={[{ id: version.id, version: version.version, status: version.status, changelog: version.changelog, created_by: version.created_by, created_at: version.created_at, updated_at: version.updated_at }]} selectedId={version.id} />
          </section>

          <section id="lane-relations" aria-labelledby="lane-relations-heading">
            <h2 id="lane-relations-heading" style={{ fontFamily: 'var(--onto-font-display)', fontSize: 'var(--onto-fs-h2)', color: 'var(--onto-ink-strong)', marginBottom: 'var(--onto-space-4)' }}>Relations</h2>
            <div style={{ backgroundColor: 'var(--onto-surface-1)', borderRadius: 'var(--onto-radius-md)', padding: 'var(--onto-space-4)' }}>
              {relationTypes.length === 0 ? (
                <p style={{ color: 'var(--onto-ink-muted)', fontSize: 'var(--onto-fs-sm)', margin: 0 }}>No relation types in this version.</p>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ borderBottom: '1px solid var(--onto-border-soft)' }}>
                    <th scope="col" style={TH_STYLE}>Relation</th>
                    <th scope="col" style={TH_STYLE}>Source</th>
                    <th scope="col" style={TH_STYLE}>Target</th>
                    <th scope="col" style={TH_STYLE}>Description</th>
                  </tr></thead>
                  <tbody>{relationTypes.map((rt) => (
                    <tr key={rt.name} style={{ borderBottom: '1px solid var(--onto-border-soft)' }}>
                      <td style={{ ...TD_STYLE, fontFamily: 'var(--onto-font-mono)', fontSize: 'var(--onto-fs-xs)' }}>{rt.name}</td>
                      <td style={{ ...TD_STYLE, fontSize: 'var(--onto-fs-xs)' }}>{(rt.source_types ?? []).join(', ') || '—'}</td>
                      <td style={{ ...TD_STYLE, fontSize: 'var(--onto-fs-xs)' }}>{(rt.target_types ?? []).join(', ') || '—'}</td>
                      <td style={{ ...TD_STYLE, maxWidth: 300 }}><span style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{rt.description ?? '—'}</span></td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
              <Link href={'/ontology?node=' + typeId} style={{ display: 'inline-block', marginTop: 'var(--onto-space-3)', color: 'var(--onto-accent-info)', fontSize: 'var(--onto-fs-sm)', textDecoration: 'none' }}>Open in viewer {'→'}</Link>
            </div>
          </section>

          <section id="lane-audit" aria-labelledby="lane-audit-heading">
            <h2 id="lane-audit-heading" style={{ fontFamily: 'var(--onto-font-display)', fontSize: 'var(--onto-fs-h2)', color: 'var(--onto-ink-strong)', marginBottom: 'var(--onto-space-4)' }}>Audit</h2>
            <div style={{ backgroundColor: 'var(--onto-surface-1)', borderRadius: 'var(--onto-radius-md)', padding: 'var(--onto-space-4)' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}><tbody>
                <tr style={{ borderBottom: '1px solid var(--onto-border-soft)' }}>
                  <td style={{ ...TD_STYLE, fontFeatureSettings: '"tnum" 1', fontSize: 'var(--onto-fs-xs)', width: 180, color: 'var(--onto-ink-muted)' }}>{new Date(version.created_at).toLocaleString('zh-CN')}</td>
                  <td style={TD_STYLE}>{version.created_by ?? 'System'}</td>
                  <td style={{ ...TD_STYLE, color: 'var(--onto-ink-muted)' }}>created v{version.version}</td>
                </tr>
                <tr>
                  <td style={{ ...TD_STYLE, fontFeatureSettings: '"tnum" 1', fontSize: 'var(--onto-fs-xs)', width: 180, color: 'var(--onto-ink-muted)' }}>{new Date(version.updated_at).toLocaleString('zh-CN')}</td>
                  <td style={TD_STYLE}>{version.created_by ?? 'System'}</td>
                  <td style={{ ...TD_STYLE, color: 'var(--onto-ink-muted)' }}>last updated</td>
                </tr>
              </tbody></table>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
