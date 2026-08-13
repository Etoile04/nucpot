/**
 * Phase 1 (NFM-267) — read-side helpers for the record_ref deep link.
 *
 * record_ref is canonical and backend-derived (PR #28 conformance firewall,
 * `build_record_ref` in `ontology_service.py`). These helpers only READ it from
 * the ontology graph payload the backend emits — they never re-derive the URL,
 * so the frontend link cannot drift from the backend contract.
 */

export interface OntologyNode {
  id: string;
  type?: string | null;
  record_ref?: string | null;
  [key: string]: unknown;
}

export interface OntologyGraph {
  nodes?: OntologyNode[];
  [key: string]: unknown;
}

/**
 * Extract the canonical record_ref deep link for a given ontology node id.
 *
 * Returns `null` when the node is absent, is not a material individual, or
 * carries no record_ref (class nodes / pre-record_ref data sources). This is
 * the graceful-no-op path — callers render the static viewer without the link.
 */
export function extractRecordRef(
  graph: OntologyGraph,
  nodeId: string,
): string | null {
  const node = (graph.nodes ?? []).find((n) => n.id === nodeId);
  if (!node) return null;
  // Only material individuals carry a record_ref deep link (PR #28 contract).
  if (node.type !== "individual") return null;
  return node.record_ref ?? null;
}
