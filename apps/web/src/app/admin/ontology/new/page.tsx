/**
 * Ontology New Page — /admin/ontology/new
 *
 * Renders OntologyEditForm with versionId=null (not a truthy sentinel).
 */
'use client'

import EditPage from '../[typeId]/edit/page'

export default function NewOntologyPage() {
  return <EditPage params={Promise.resolve({ typeId: '' })} />
}
