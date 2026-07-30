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

import { Tag } from "antd"

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
  manual: "default",
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

/** Stable label for an unresolved node id (AC-2 fallback).
 *
 * Keeps the full id so a missing source/target can still be referenced
 * in a bug report (e.g. "edge points at kg-node-…"). For the rare
 * full-UUID fallback case, callers may rely on CSS truncation in the
 * surrounding flex container to keep the row height bounded.
 */
function shortId(id: string): string {
  return id
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
    <div className="text-sm">
      <span data-testid="edge-source">{sourceLabel}</span>
      <span className="mx-1 text-gray-400">--</span>
      <span data-testid="edge-relation" className="font-mono">
        {edge.property_name}
      </span>
      <span className="mx-1 text-gray-400">--&gt;</span>
      <span data-testid="edge-target">{targetLabel}</span>
      <span className="ml-1 text-gray-400">→</span>
    </div>
  )
}

export function ExtractionResultsList({ items }: ExtractionResultsListProps) {
  const labelIndex = buildKgNodeLabelIndex(items)

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
            <div className="flex items-center gap-2 mb-1">
              {knownSourceType !== null && (
                <Tag color={SOURCE_TYPE_COLOR[knownSourceType]}>
                  {SOURCE_TYPE_LABEL[knownSourceType]}
                </Tag>
              )}
              {knownSourceType !== "kg_edge" && item.item_type && (
                <Tag color="blue">{item.item_type}</Tag>
              )}
              {knownSourceType !== "kg_edge" && (
                <span className="font-medium">{item.property_name}</span>
              )}
              {item.confidence != null && (
                <Tag color="default">
                  置信度 {(item.confidence * 100).toFixed(0)}%
                </Tag>
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
          <div className="text-gray-500 italic mt-1">
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
          <div className="text-gray-500 italic mt-1">
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