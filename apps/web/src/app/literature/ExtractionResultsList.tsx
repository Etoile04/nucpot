"use client"

/**
 * ExtractionResultsList — renders one extraction-result row.
 *
 * Carved out of LiteratureManager.tsx so the source-type provenance Tag
 * (NFM-2249 AC-1) and the readable KG-edge triple (NFM-2249 AC-2) can
 * be unit-tested in isolation. Degrades gracefully (NFM-2249 AC-5) when
 * an item lacks `source_type` (the pre-PR-#552 prod shape).
 *
 * Pure presentational component: receives items, renders rows. Parent
 * owns layout / sizing.
 */

import { Empty, Tag } from "antd"

import type {
  ExtractionResultItem,
  KgEdgeExtractionResultItem,
} from "@/lib/api-client"

interface ExtractionResultsListProps {
  readonly items: readonly ExtractionResultItem[]
}

/** Human-readable label + Ant Design color per `source_type`. */
const SOURCE_TYPE_LABEL: Record<"manual" | "kg_node" | "kg_edge", string> = {
  manual: "手动录入",
  kg_node: "KG 实体",
  kg_edge: "KG 关系",
}

const SOURCE_TYPE_COLOR: Record<"manual" | "kg_node" | "kg_edge", string> = {
  // `gold` rather than `default`: the confidence and review_status pills
  // were also neutral grey, so a manual row showed three near-identical
  // pills and the provenance signal stopped reading as provenance.
  // `blue` is unavailable — `item_type` already owns it.
  manual: "gold",
  kg_node: "cyan",
  kg_edge: "purple",
}

/**
 * Build a `nodeId → label` index over the kg_node items so a kg_edge row
 * can resolve its source / target labels. Edges reference node ids; the
 * same response carries both nodes and edges.
 */
export function buildKgNodeLabelIndex(
  items: readonly ExtractionResultItem[],
): Map<string, string> {
  const index = new Map<string, string>()
  for (const item of items) {
    if (item.source_type === "kg_node") {
      index.set(item.id, item.property_name)
    }
  }
  return index
}

/**
 * Stable label for an unresolved node id (AC-2 fallback).
 *
 * Orphan endpoints are routine: the backend caps nodes at 200 and
 * edges at 400 (literature.py `_MAX_KG_NODES_PER_SOURCE` /
 * `_MAX_KG_EDGES_PER_SOURCE`), so densely-extracted papers routinely
 * surface edges whose endpoints were truncated out of the node page.
 * Rendering those 36-char UUIDs verbatim wraps the row and breaks the
 * visual rhythm of the panel.
 *
 * Truncates to the first 8 chars + ellipsis whenever the id is longer
 * than `_SHORT_ID_THRESHOLD` (12). 8 hex chars are enough to identify
 * a specific UUID within a small list and to copy into a bug report.
 * Short ids (test fixtures, short slugs) pass through unchanged so we
 * don't pepper the UI with ellipsis on already-readable labels.
 *
 * The row container also applies `truncate` / `min-w-0` / `shrink`
 * classes (see `KgEdgeRow` below) so the resolved label still ellipsises
 * gracefully when the row itself is narrower than the truncated form.
 */
const _SHORT_ID_THRESHOLD = 12
const _SHORT_ID_PREFIX_LENGTH = 8

function shortId(id: string): string {
  if (id.length <= _SHORT_ID_THRESHOLD) {
    return id
  }
  return `${id.slice(0, _SHORT_ID_PREFIX_LENGTH)}…`
}

interface KgEdgeRowProps {
  readonly edge: KgEdgeExtractionResultItem
  readonly labelIndex: ReadonlyMap<string, string>
}

function KgEdgeRow({ edge, labelIndex }: KgEdgeRowProps) {
  const sourceLabel =
    labelIndex.get(edge.source_node_id ?? "") ??
    shortId(edge.source_node_id ?? "?")
  const targetLabel =
    labelIndex.get(edge.source_target_id ?? "") ??
    shortId(edge.source_target_id ?? "?")

  return (
    <div
      className="text-sm flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 min-w-0"
      role="group"
      aria-label={`${sourceLabel} ${edge.property_name} ${targetLabel}`}
    >
      {/*
        `shrink` and NOT `flex-1`. `flex-1` is `flex: 1 1 0%` — a zero
        basis makes both endpoint boxes the same width regardless of
        content, so a long target clipped while space sat unused beside
        a short source. `shrink` sizes each box to its own content.

        `flex-wrap` then covers the narrow case: at 390px a full triple
        genuinely exceeds one line, and a node label is data. Wrapping
        the target onto a second line keeps it whole, where ellipsis
        would silently eat the tail of e.g. `thermal_conductivity`.
        `truncate` remains as the last resort for a single label wider
        than the whole row.
      */}
      <span
        data-testid="edge-source"
        className="truncate min-w-0 shrink"
        title={edge.source_node_id ?? undefined}
      >
        {sourceLabel}
      </span>
      <span className="text-gray-500 shrink-0" aria-hidden="true">
        →
      </span>
      <span
        data-testid="edge-relation"
        className="font-mono text-xs text-gray-600 shrink-0"
      >
        {edge.property_name}
      </span>
      <span className="text-gray-500 shrink-0" aria-hidden="true">
        →
      </span>
      <span
        data-testid="edge-target"
        className="truncate min-w-0 shrink"
        title={edge.source_target_id ?? undefined}
      >
        {targetLabel}
      </span>
      {/* Absorbs slack after the triple so the endpoints stay adjacent
          to their predicate instead of being pushed to the rails. */}
      <span className="flex-1" aria-hidden="true" />
    </div>
  )
}

export function ExtractionResultsList({ items }: ExtractionResultsListProps) {
  const labelIndex = buildKgNodeLabelIndex(items)

  if (items.length === 0) {
    return (
      <div data-testid="extraction-empty" className="py-6">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无抽取结果"
        />
      </div>
    )
  }

  return (
    <div className="space-y-2 max-h-96 overflow-y-auto">
      {items.map((item) => {
        // AC-5 graceful degradation: items missing `source_type` render
        // their common fields but no provenance Tag.
        const sourceType = (item as { source_type?: string }).source_type
        const knownSourceType: "manual" | "kg_node" | "kg_edge" | null =
          sourceType === "manual" ||
          sourceType === "kg_node" ||
          sourceType === "kg_edge"
            ? sourceType
            : null

        return (
          <div
            key={item.id}
            data-testid={`extraction-row-${item.id}`}
            className="border border-gray-200 rounded p-2 text-xs"
          >
            {/*
              `flex-wrap` is load-bearing: without it this header
              overflowed its container by up to 67px at 390x844, clipping
              `置信度 92%` mid-glyph. The detail panel is narrower than
              the viewport, so the header must be allowed a second line.
            */}
            <div
              data-testid="extraction-header"
              className="flex flex-wrap items-center gap-2 mb-1 min-w-0"
            >
              {knownSourceType !== null && (
                <Tag color={SOURCE_TYPE_COLOR[knownSourceType]}>
                  {SOURCE_TYPE_LABEL[knownSourceType]}
                </Tag>
              )}
              {knownSourceType !== "kg_edge" && item.item_type && (
                <Tag color="blue">{item.item_type}</Tag>
              )}
              {knownSourceType !== "kg_edge" && (
                <span className="font-medium truncate min-w-0">
                  {item.property_name}
                </span>
              )}
              {/* Plain text, not a Tag — keeps provenance the only
                  coloured pill in the row (AC-1 "distinct treatment"). */}
              {item.confidence != null && (
                <span className="text-gray-500">
                  置信度 {(item.confidence * 100).toFixed(0)}%
                </span>
              )}
              {knownSourceType === "manual" &&
                "review_status" in item &&
                item.review_status != null && <Tag>{item.review_status}</Tag>}
            </div>

            {knownSourceType === "kg_edge" ? (
              <KgEdgeRow
                edge={item as KgEdgeExtractionResultItem}
                labelIndex={labelIndex}
              />
            ) : (
              <RowBody item={item} />
            )}
          </div>
        )
      })}
    </div>
  )
}

function RowBody({ item }: { item: ExtractionResultItem }) {
  // No `source_type` (legacy prod shape) — render common fields only.
  if (!("source_type" in item) || item.source_type === undefined) {
    return renderCommonBody(item)
  }

  if (item.source_type === "manual") {
    return (
      <>
        {renderCommonBody(item)}
        {item.source_paragraph != null && (
          <div className="text-gray-500 italic mt-1 leading-relaxed">
            「{item.source_paragraph.substring(0, 200)}」
          </div>
        )}
      </>
    )
  }

  if (item.source_type === "kg_node") {
    return (
      <>
        {renderCommonBody(item)}
        {"unit" in item && item.unit != null && item.value == null && (
          <div className="text-gray-500 text-xs">单位：{item.unit}</div>
        )}
        {item.source_paragraph != null && (
          <div className="text-gray-500 italic mt-1 leading-relaxed">
            「{item.source_paragraph.substring(0, 200)}」
          </div>
        )}
      </>
    )
  }

  return null
}

function renderCommonBody(item: ExtractionResultItem) {
  return (
    <>
      {item.value != null && (
        <pre className="bg-gray-50 p-2 rounded text-xs overflow-x-auto">
          {JSON.stringify(item.value, null, 2)}
        </pre>
      )}
    </>
  )
}