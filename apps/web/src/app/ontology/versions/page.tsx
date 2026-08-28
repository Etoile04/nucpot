import { Metadata } from 'next'
import '@/features/ontology/tokens.css'
import { OntologyListPage } from '@/features/ontology/components/OntologyListPage'

export const metadata: Metadata = {
  title: '本体版本管理 - NFMD',
  description: '管理和发布本体版本',
}

export default function OntologyVersionsPage() {
  return <OntologyListPage />
}
