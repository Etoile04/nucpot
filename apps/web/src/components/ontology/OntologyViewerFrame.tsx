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

/**
 * Reusable host container styles — fills the parent flex area exactly.
 * The parent <main class="flex-1 overflow-y-auto"> provides the available
 * height after the nav bar; we use height:100% to inherit it and min-height:0
 * to allow the flex item to shrink below content size on short viewports.
 * (Previously min-height:600px forced the iframe taller than the viewport on
 * screens < 665px, causing the bottom of the viewer to be clipped by body's
 * overflow:hidden — appearing as a dark overlay bar.)
 */
const HOST_CONTAINER_STYLE = {
  width: "100%",
  height: "100%",
  minHeight: "0",
  flex: "1",
} as const;

/** Reusable iframe chromeless styles — fills host, no border. */
const IFRAME_STYLE = {
  width: "100%",
  height: "100%",
  border: "0",
} as const;

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
  /**
   * Corpus id for dynamic data source resolution (NFM-610). When provided,
   * the viewer's corpus selector resolves the data URL from the corpus index
   * manifest instead of using the hardcoded static corpus.
   */
  corpus?: string;
  /**
   * Whether the record_ref graph fetch is in progress (NFM-610).
   * When true, shows a loading hint below the viewer.
   */
  loading?: boolean;
}

/** Build the determinate iframe src for the embedded viewer. */
export function buildOntologyViewerSrc(
  node?: string,
  corpus?: string,
): string {
  const params = new URLSearchParams();
  params.set("embed", "false");

  // Corpus param takes precedence — viewer resolves data URL from corpus manifest.
  if (corpus) {
    params.set("corpus", corpus);
  } else {
    params.set("data", DEFAULT_DATA_URL);
  }

  if (node) {
    params.set("node", node);
  }
  return `${VIEWER_ENTRY}?${params.toString()}`;
}

export default function OntologyViewerFrame({
  node,
  recordRef,
  corpus,
  loading,
}: OntologyViewerFrameProps) {
  return (
    <div style={HOST_CONTAINER_STYLE}>
      <iframe
        src={buildOntologyViewerSrc(node, corpus)}
        title="OntoFuel 本体可视化"
        loading="lazy"
        allowFullScreen
        style={IFRAME_STYLE}
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
      {loading && !recordRef && (
        <span
          className="ontology-record-ref-loading"
          style={{ fontSize: "0.85em", color: "#888" }}
        >
          Loading material records…
        </span>
      )}
    </div>
  );
}
