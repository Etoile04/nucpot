/**
 * Ontology New Page — /admin/ontology/new
 *
 * Reuses OntologyEditForm with no versionId.
 */
'use client'

import EditPage from '../[typeId]/edit/page'

export default function NewOntologyPage() {
  return <EditPage params={Promise.resolve({ typeId: '__new__' })} />
}
