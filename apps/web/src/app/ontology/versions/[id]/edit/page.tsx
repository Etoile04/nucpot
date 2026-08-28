import { Metadata } from 'next'
import '@/features/ontology/tokens.css'

export const metadata: Metadata = {
  title: '编辑本体版本 - NFMD',
  description: '编辑本体版本草稿',
}

/**
 * Placeholder for P2 — Edit page.
 * NFM-3781 P1 scope: route renders, shows placeholder.
 */
export default function OntologyVersionEditPage() {
  return (
    <div
      className="flex items-center justify-center"
      style={{
        minHeight: '60vh',
        background: 'var(--ontology-bg-primary)',
        color: 'var(--ontology-text-secondary)',
      }}
    >
      <p style={{ fontSize: 'var(--ontology-text-lg)' }}>
        编辑版本页（P2 开发中）
      </p>
    </div>
  )
}
