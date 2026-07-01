"use client";

/**
 * OntologyViewerFrame — embeds the OntoFuel NVL viewer as a same-origin iframe.
 *
 * Contract-as-firewall invariant (NFM-246 ADR): the viewer is stable across
 * phases; only the data source changes. Phase 0 points `data=` at the vendored
 * static corpus (`/ontology-viewer/data/nvl_ontology_data.json`). Phase 1 will
 * only swap this URL for the backend NVL API — no page/component rewrite.
 *
 * The viewer resolves `?data=` > env > default `/data/...`. Because we serve the
 * corpus under `/ontology-viewer/data/`, the `data` param is determinate and
 * REQUIRED here (otherwise the viewer fetches the root default and 404s). The
 * `node` prop carries the `?node=<id>` deep link (NFM-237 MUST #3), resolved
 * server-side in the page so Phase 0 stays static. Height contract (§5) is pure
 * CSS: the host gives the iframe an explicit `min-height: 600px` and the viewer
 * fills via its `height: 100%` chain — no postMessage protocol.
 *
 * See EMBEDDING.md §1–§5 in the viewer repo (commit 6345543).
 */

const VIEWER_ENTRY = "/ontology-viewer/index.html";
const DEFAULT_DATA_URL = "/ontology-viewer/data/nvl_ontology_data.json";

export interface OntologyViewerFrameProps {
  /** Optional node id for deep-linking (?node=<id>, NFM-237 MUST #3). */
  node?: string;
  /**
   * Phase 2 (NFM-267): stable, origin-relative deep link from a material node
   * to its property records. When present, rendered as a shareable "View
   * material records" anchor below the viewer. Omitted for class nodes and
   * pre-record_ref data sources (Phase 0 static corpus) — graceful no-op.
   */
  recordRef?: string;
}

/** Build the determinate iframe src for the embedded viewer. */
export function buildOntologyViewerSrc(node?: string): string {
  const src = `${VIEWER_ENTRY}?embed=false&data=${DEFAULT_DATA_URL}`;
  return node ? `${src}&node=${encodeURIComponent(node)}` : src;
}

export default function OntologyViewerFrame({
  node,
  recordRef,
}: OntologyViewerFrameProps) {
  return (
    <div style={{ width: "100%", minHeight: "600px" }}>
      <iframe
        src={buildOntologyViewerSrc(node)}
        title="OntoFuel 本体可视化"
        loading="lazy"
        allowFullScreen
        style={{
          width: "100%",
          height: "100%",
          minHeight: "600px",
          border: "0",
        }}
      />
      {/* Phase 2 (NFM-267): shareable deep link from node → material records.
          Omitted for class nodes and pre-record_ref data sources. */}
      {recordRef && (
        <a
          href={recordRef}
          className="ontology-record-ref-link"
          target="_blank"
          rel="noreferrer noopener"
        >
          View material records →
        </a>
      )}
    </div>
  );
}
