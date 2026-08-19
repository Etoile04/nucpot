"use client";

/**
 * OntologyCorpusBrowser — `/ontology` page container (NFM-3325).
 *
 * Wraps the vendored OntoFuel viewer iframe with:
 *   - a sidebar corpus selector that drives the iframe's `?corpus=` param
 *     and syncs to the page URL so deep links survive reload / share
 *   - graceful failure: if the corpus index fails to load (or backend is
 *     unreachable) the sidebar is hidden and the viewer falls back to its
 *     own manifest fetch / static corpus
 *
 * Replaces the previous chain: `OntologyRecordRef →` → `OntologyViewerFrame`
 * which always pinned `?data=...nvl_ontology_data.json` and silently
 * bypassed the viewer's corpus dropdown (the bug NFM-3303/3325 fixes).
 */

import { useEffect, useState } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import OntologyViewerFrame from "./OntologyViewerFrame";
import { extractRecordRef, type OntologyGraph } from "@/lib/ontology/record-ref";

interface CorpusEntry {
  id: string;
  name: string;
  description?: string;
  source_digest?: string;
  asset_url?: string;
  schema_version?: string;
}

interface CorpusIndexResponse {
  corpora: CorpusEntry[];
  default_corpus?: string;
}

type FetchStatus = "idle" | "loading" | "done";

export interface OntologyCorpusBrowserProps {
  /** Optional node id from ?node= deep link. */
  node?: string;
  /** Corpus id from ?corpus= deep link. */
  corpus?: string;
}

const SELECTOR_HEIGHT = 65; // matches /ontology page top offset

const SIDEBAR_STYLE = {
  width: "260px",
  minWidth: "260px",
  height: `calc(100vh - ${SELECTOR_HEIGHT}px)`,
  borderRight: "1px solid #303030",
  background: "#1f1f1f",
  color: "#e5e5e5",
  overflowY: "auto" as const,
  padding: "16px",
  boxSizing: "border-box" as const,
};

const IFRAME_WRAP_STYLE = {
  flex: "1",
  height: `calc(100vh - ${SELECTOR_HEIGHT}px)`,
  minWidth: "0",
} as const;

const SELECT_STYLE = {
  width: "100%",
  background: "#2a2a2a",
  color: "#f5f5f5",
  border: "1px solid #404040",
  borderRadius: "4px",
  padding: "6px 8px",
  fontSize: "14px",
} as const;

const EMPTY_STYLE = {
  padding: "12px 0",
  color: "#888",
  fontSize: "12px",
} as const;

export default function OntologyCorpusBrowser({
  node,
  corpus,
}: OntologyCorpusBrowserProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [corpora, setCorpora] = useState<CorpusEntry[] | null>(null);
  const [defaultCorpus, setDefaultCorpus] = useState<string>("default");
  const [fetchStatus, setFetchStatus] = useState<FetchStatus>("idle");
  const [recordRef, setRecordRef] = useState<string | null>(null);
  const [recordRefStatus, setRecordRefStatus] = useState<FetchStatus>("idle");

  // Fetch corpus index. Endpoint is the same-origin Next.js proxy that NFM-3303
  // rewrote into the dynamic aggregator — it 200s with the static default if
  // the backend is down (fail-soft contract).
  useEffect(() => {
    let cancelled = false;
    setFetchStatus("loading");
    fetch("/api/proxy/ontology/corpora", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: CorpusIndexResponse | null) => {
        if (cancelled) return;
        if (data && Array.isArray(data.corpora)) {
          setCorpora(data.corpora);
          if (data.default_corpus) setDefaultCorpus(data.default_corpus);
        }
      })
      .catch(() => {
        // Silent — viewer has its own internal fallback to the static corpus.
      })
      .finally(() => {
        if (!cancelled) setFetchStatus("done");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // record_ref deep link (Phase 2, NFM-267) — only when both node + corpus.
  useEffect(() => {
    if (!node || !corpus) {
      setRecordRef(null);
      setRecordRefStatus("idle");
      return;
    }
    const controller = new AbortController();
    setRecordRefStatus("loading");
    fetch(
      `/api/v1/ontology/corpora/${encodeURIComponent(corpus)}/graph`,
      { signal: controller.signal },
    )
      .then((r) => (r.ok ? r.json() : null))
      .then((graph: OntologyGraph | null) => {
        if (controller.signal.aborted || !graph) return;
        setRecordRef(extractRecordRef(graph, node));
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
      })
      .finally(() => {
        if (!controller.signal.aborted) setRecordRefStatus("done");
      });
    return () => controller.abort();
  }, [node, corpus]);

  // Sidebar change → push to URL (preserves ?node= etc.) so reload/share work.
  const handleCorpusChange = (next: string) => {
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    if (next && next !== defaultCorpus) {
      params.set("corpus", next);
    } else {
      params.delete("corpus");
    }
    const qs = params.toString();
    router.push(`${pathname}${qs ? `?${qs}` : ""}`);
  };

  return (
    <div
      style={{
        position: "fixed",
        top: SELECTOR_HEIGHT,
        left: 0,
        right: 0,
        bottom: 0,
        display: "flex",
        overflow: "hidden",
      }}
    >
      <aside style={SIDEBAR_STYLE} aria-label="Corpus selector">
        <div style={{ fontSize: "12px", color: "#999", marginBottom: "6px" }}>
          Corpus
        </div>
        <select
          data-testid="ontology-corpus-select"
          aria-label="Select corpus"
          style={SELECT_STYLE}
          value={corpus ?? defaultCorpus}
          onChange={(e) => handleCorpusChange(e.target.value)}
        >
          {/* Sidebar renders even while loading (shows default option); the
             iframe's own dropdown will populate once NFM-3303's aggregator
             responds. This guarantees a non-empty UI on slow networks. */}
          {fetchStatus === "loading" && (!corpora || corpora.length === 0) ? (
            <option value={defaultCorpus}>加载中…</option>
          ) : null}
          {corpora?.map((c) => (
            <option key={c.id} value={c.id} title={c.description ?? c.name}>
              {c.name} ({c.id})
            </option>
          ))}
          {corpora === null && (
            <option value={defaultCorpus}>{defaultCorpus} (静态)</option>
          )}
        </select>

        {corpora && (
          <div style={{ marginTop: "16px", fontSize: "12px", color: "#aaa" }}>
            <div>共 {corpora.length} 个语料库</div>
            {corpus && corpus !== defaultCorpus && (
              <div style={{ marginTop: "8px", color: "#80aaff" }}>
                当前: {corpus}
              </div>
            )}
          </div>
        )}

        {corpora === null && fetchStatus === "done" && (
          <div style={EMPTY_STYLE}>
            无法连接语料库服务，请稍后重试。
          </div>
        )}
      </aside>

      <div style={IFRAME_WRAP_STYLE}>
        <OntologyViewerFrame
          node={node}
          corpus={corpus}
          recordRef={recordRef ?? undefined}
          loading={recordRefStatus === "loading"}
        />
      </div>
    </div>
  );
}