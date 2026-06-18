"use client";

/**
 * OntologyRecordRef — Phase 1 (NFM-267) live-page wiring for the record_ref
 * deep link.
 *
 * The static OntoFuel viewer iframe loads immediately from the vendored corpus
 * (Phase 0, unchanged). The "View material records →" link is progressive
 * enhancement: it consumes the CANONICAL record_ref from the backend ontology
 * graph (contract-faithful — never recomputed client-side, per the PR #28
 * conformance firewall) and surfaces it via OntologyViewerFrame.recordRef.
 *
 * Graceful degradation: the link is omitted until the graph resolves, on fetch
 * failure, or for non-individual nodes — the viewer stays fully usable.
 */

import { useEffect, useState } from "react";
import OntologyViewerFrame from "./OntologyViewerFrame";
import { extractRecordRef, type OntologyGraph } from "@/lib/ontology/record-ref";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface OntologyRecordRefProps {
  /** Node id from the ?node= deep link (e.g. "mat:UO2"). */
  node?: string;
  /** Corpus id from ?corpus= (e.g. "smirnov2014"). Required for a record_ref. */
  corpus?: string;
}

export default function OntologyRecordRef({
  node,
  corpus,
}: OntologyRecordRefProps) {
  const [recordRef, setRecordRef] = useState<string | null>(null);

  useEffect(() => {
    // No fetch without a concrete node + corpus — Phase 0 static browse path.
    if (!node || !corpus) return;
    let cancelled = false;

    fetch(
      `${API_BASE}/api/v1/ontology/corpora/${encodeURIComponent(corpus)}/graph`,
    )
      .then((response) => (response.ok ? response.json() : null))
      .then((graph: OntologyGraph | null) => {
        if (cancelled || !graph) return;
        setRecordRef(extractRecordRef(graph, node));
      })
      .catch(() => {
        /* graceful: leave the viewer link-free on fetch error */
      });

    return () => {
      cancelled = true;
    };
  }, [node, corpus]);

  return <OntologyViewerFrame node={node} recordRef={recordRef ?? undefined} />;
}
