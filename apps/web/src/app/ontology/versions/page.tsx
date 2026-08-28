import { Suspense } from 'react'
import { Metadata } from 'next'
import '@/features/ontology/tokens.css'
import { OntologyListPage } from '@/features/ontology/components/OntologyListPage'

export const metadata: Metadata = {
  title: '本体版本管理 - NFMD',
  description: '管理和发布本体版本',
}

function ListPageFallback() {
  return (
    <div
      style={{
        minHeight: '60vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--ontology-bg-primary)',
        color: 'var(--ontology-text-muted)',
      }}
    >
      <p>加载中...</p>
    </div>
  )
}

export default function OntologyVersionsPage() {
  return (
    <Suspense fallback={<ListPageFallback />}>
      <OntologyListPage />
    </Suspense>
  )
}
