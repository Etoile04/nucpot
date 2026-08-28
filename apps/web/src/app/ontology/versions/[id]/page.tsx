import { Metadata } from 'next'
import '@/features/ontology/tokens.css'

export const metadata: Metadata = {
  title: '本体版本详情 - NFMD',
  description: '查看本体版本详情',
}

/**
 * Placeholder for P2 — Detail page.
 * NFM-3781 P1 scope: route renders, shows placeholder.
 */
export default function OntologyVersionDetailPage() {
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
        版本详情页（P2 开发中）
      </p>
    </div>
  )
}
