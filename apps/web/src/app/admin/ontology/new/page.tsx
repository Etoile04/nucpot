/**
 * Ontology New Page — /admin/ontology/new
 *
 * Reuses OntologyEditForm with no versionId.
 */
'use client'

import { OntologyEditForm } from './[typeId]/edit/page'

export default function NewOntologyPage() {
  return <OntologyEditForm versionId={null} />
}
