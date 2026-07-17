/**
 * KG Node Detail page content (NFM-1337).
 *
 * Fetches a single KG node by {type}/{id} and renders:
 *   - header: type badge, label, confidence badge, aliases
 *   - properties table with confidence badges
 *   - source references
 *   - sidebar: incoming + outgoing relations; clicking one navigates
 *     to the neighbour's detail page via /kg/nodes/{type}/{id}
 *
 * Accessibility: keyboard accessible (native button + list semantics),
 * prefers-reduced-motion honored via the `data-reduced-motion` attribute
 * on the root container for QA hooks. Layout-bound properties are not
 * animated; only opacity/transition for color changes are used.
 */

"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Empty, Skeleton, Typography } from "antd"
import { ArrowLeftOutlined } from "@ant-design/icons"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import {
  fetchKgNodeDetail,
  type KgNodeDetail,
  type KgRelationItem,
} from "@/lib/kg-node-api"
import { kgNodeTypeClass } from "@/lib/kg-node-theme"

const { Title, Text } = Typography

interface KgNodeDetailContentProps {
  readonly type: string
  readonly id: string
}

interface DetailState {
  readonly status: "loading" | "ok" | "error"
  readonly node: KgNodeDetail | null
  readonly error: string | null
}

const INITIAL: DetailState = { status: "loading", node: null, error: null }

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return ""
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function RelationRow({ item }: { readonly item: KgRelationItem }) {
  const arrow = item.direction === "outgoing" ? "→" : "←"
  const href = `/kg/nodes/${item.neighbour.node_type}/${item.neighbour.id}`
  return (
    <li className="border-b border-[var(--border-color,#2d2d44)] last:border-b-0">
      <Link
        href={href}
        className="block w-full text-left p-3 hover:bg-[var(--bg-elevated-hover,#22223a)] rounded transition-colors duration-150 group focus:outline-none focus:ring-2 focus:ring-blue-500/50"
      >
        <div className="flex items-center gap-2 mb-1">
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${kgNodeTypeClass(item.neighbour.node_type)}`}
          >
            {item.neighbour.node_type}
          </span>
          <span className="text-xs font-mono text-gray-400">
            {arrow} {item.relation_type}
          </span>
          <ConfidenceBadge value={item.confidence} />
        </div>
        <div className="text-white text-sm font-medium group-hover:text-blue-300 transition-colors truncate">
          {item.neighbour.label}
        </div>
      </Link>
    </li>
  )
}

export function KgNodeDetailContent({ type, id }: KgNodeDetailContentProps) {
  const router = useRouter()
  const [state, setState] = useState<DetailState>(INITIAL)

  const load = useCallback(() => {
    if (!type || !id) {
      setState({
        status: "error",
        node: null,
        error: "Missing route parameters",
      })
      return
    }
    setState(INITIAL)
    fetchKgNodeDetail({ type, id })
      .then((node) => setState({ status: "ok", node, error: null }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : "Load failed"
        setState({ status: "error", node: null, error: message })
      })
  }, [type, id])

  useEffect(() => {
    load()
  }, [load])

  const handleBack = useCallback(() => router.back(), [router])

  // ── Loading ────────────────────────────────────────────────────────
  if (state.status === "loading") {
    return (
      <main
        data-testid="kg-node-detail-loading"
        role="status"
        aria-busy="true"
        aria-label="Loading knowledge graph node"
        className="max-w-[1200px] mx-auto px-6 py-8"
      >
        {/* Header skeleton: back link + type badge + title + subtitle */}
        <Skeleton.Button
          active
          size="small"
          className="!w-16 !h-6 !rounded mb-6"
          aria-hidden
        />
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <Skeleton.Input
            active
            size="small"
            className="!w-20 !h-6 !rounded"
            aria-hidden
          />
          <Skeleton.Input
            active
            size="small"
            className="!w-24 !h-6 !rounded"
            aria-hidden
          />
          <Skeleton.Input
            active
            size="small"
            className="!w-16 !h-6 !rounded"
            aria-hidden
          />
        </div>
        <Skeleton.Input
          active
          size="large"
          className="!w-2/3 !h-10 !rounded mb-2"
          aria-hidden
        />
        <Skeleton.Input
          active
          size="small"
          className="!w-1/3 !h-4 !rounded mb-10"
          aria-hidden
        />

        {/* Body skeleton: main column + sidebar */}
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-8">
          <div className="space-y-6">
            <Skeleton.Input
              active
              size="small"
              className="!w-32 !h-6 !rounded"
              aria-hidden
            />
            <Skeleton
              active
              paragraph={{
                rows: 4,
                width: ["60%", "80%", "55%", "70%"],
              }}
              title={false}
            />
            <Skeleton.Input
              active
              size="small"
              className="!w-40 !h-6 !rounded !mt-8"
              aria-hidden
            />
            <Skeleton
              active
              paragraph={{ rows: 2, width: ["90%", "75%"] }}
              title={false}
            />
          </div>
          <aside className="space-y-4">
            <Skeleton.Input
              active
              size="small"
              className="!w-28 !h-6 !rounded"
              aria-hidden
            />
            <Skeleton
              active
              paragraph={{ rows: 3, width: ["85%", "90%", "60%"] }}
              title={false}
            />
            <Skeleton.Input
              active
              size="small"
              className="!w-24 !h-6 !rounded !mt-4"
              aria-hidden
            />
            <Skeleton
              active
              paragraph={{ rows: 2, width: ["80%", "70%"] }}
              title={false}
            />
          </aside>
        </div>
      </main>
    )
  }

  // ── Error ──────────────────────────────────────────────────────────
  if (state.status === "error") {
    return (
      <main className="max-w-[1200px] mx-auto px-6 py-8">
        <button
          type="button"
          onClick={handleBack}
          className="inline-flex items-center gap-1 text-gray-400 hover:text-white mb-6 focus:outline-none focus:ring-2 focus:ring-blue-500/50 rounded"
        >
          <ArrowLeftOutlined /> Back
        </button>
        <div className="flex flex-col items-center justify-center min-h-[300px] gap-4">
          <Text type="danger">{state.error ?? "Unknown error"}</Text>
          <button
            type="button"
            onClick={load}
            className="px-4 py-2 rounded bg-blue-600 text-white hover:bg-blue-500 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500/50"
          >
            Retry
          </button>
        </div>
      </main>
    )
  }

  // ── Loaded ─────────────────────────────────────────────────────────
  const node = state.node
  if (!node) {
    return null
  }

  const propertyEntries = Object.entries(node.properties ?? {})

  return (
    <main
      data-testid="kg-node-detail-root"
      data-reduced-motion="honored"
      className="max-w-[1200px] mx-auto px-6 py-8 transition-opacity duration-150"
    >
      {/* Back */}
      <button
        type="button"
        onClick={handleBack}
        aria-label="Back to previous page"
        className="inline-flex items-center gap-1 text-gray-400 hover:text-white mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500/50 rounded"
      >
        <ArrowLeftOutlined /> Back
      </button>

      {/* Header */}
      <header className="mb-8">
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${kgNodeTypeClass(node.node_type)}`}
          >
            {node.node_type}
          </span>
          <ConfidenceBadge value={node.confidence} size="md" showLabel />
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono bg-gray-700/50 text-gray-400 border border-gray-600/30">
            {node.status}
          </span>
        </div>
        <Title level={1} className="!m-0 text-white">
          {node.label}
        </Title>
        {node.aliases.length > 0 && (
          <Text type="secondary">Also known as: {node.aliases.join(", ")}</Text>
        )}
      </header>

      {/* Body grid: main + sidebar */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-8">
        {/* Main content */}
        <div className="space-y-8">
          {/* Properties */}
          <section aria-labelledby="properties-heading">
            <Title level={3} id="properties-heading" className="!text-white">
              Properties
            </Title>
            {propertyEntries.length === 0 ? (
              <Empty
                description={
                  <Text type="secondary">No properties recorded.</Text>
                }
              />
            ) : (
              <div className="overflow-x-auto rounded-lg border border-[var(--border-color,#2d2d44)]">
                <table className="w-full text-sm">
                  <thead className="bg-[var(--bg-elevated,#1a1a2e)] text-left">
                    <tr>
                      <th
                        scope="col"
                        className="px-4 py-2 font-medium text-gray-300"
                      >
                        Key
                      </th>
                      <th
                        scope="col"
                        className="px-4 py-2 font-medium text-gray-300"
                      >
                        Value
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {propertyEntries.map(([key, value]) => (
                      <tr
                        key={key}
                        className="border-t border-[var(--border-color,#2d2d44)]"
                      >
                        <td className="px-4 py-2 font-mono text-gray-400 align-top">
                          {key}
                        </td>
                        <td className="px-4 py-2 text-white break-words">
                          {formatValue(value)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Source references */}
          <section aria-labelledby="sources-heading">
            <Title level={3} id="sources-heading" className="!text-white">
              Source References
            </Title>
            {node.sources.length === 0 ? (
              <Empty
                description={
                  <Text type="secondary">No source references.</Text>
                }
              />
            ) : (
              <ul className="space-y-2">
                {node.sources.map((src, idx) => (
                  <li
                    key={`${src.source_id ?? "src"}-${idx}`}
                    className="px-4 py-2 rounded bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)]"
                  >
                    <div className="text-white text-sm">{src.label}</div>
                    {src.source_id && (
                      <div className="text-xs font-mono text-gray-500 mt-1 break-all">
                        source_id: {src.source_id}
                        {src.figure_id ? ` · figure: ${src.figure_id}` : ""}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {/* Sidebar — relations */}
        <aside
          aria-labelledby="relations-heading"
          className="lg:sticky lg:top-6 lg:self-start"
        >
          <Title level={3} id="relations-heading" className="!text-white">
            Relations
          </Title>

          {/* Outgoing */}
          <section
            aria-labelledby="outgoing-heading"
            className="mb-6 rounded-lg border border-[var(--border-color,#2d2d44)] bg-[var(--bg-elevated,#1a1a2e)]"
          >
            <h4
              id="outgoing-heading"
              className="px-4 py-2 text-sm font-medium text-gray-300 border-b border-[var(--border-color,#2d2d44)]"
            >
              Outgoing ({node.relations.outgoing.length})
            </h4>
            {node.relations.outgoing.length === 0 ? (
              <p className="px-4 py-3 text-sm text-gray-500">None.</p>
            ) : (
              <ul role="list">
                {node.relations.outgoing.map((rel) => (
                  <RelationRow key={rel.edge_id} item={rel} />
                ))}
              </ul>
            )}
          </section>

          {/* Incoming */}
          <section
            aria-labelledby="incoming-heading"
            className="rounded-lg border border-[var(--border-color,#2d2d44)] bg-[var(--bg-elevated,#1a1a2e)]"
          >
            <h4
              id="incoming-heading"
              className="px-4 py-2 text-sm font-medium text-gray-300 border-b border-[var(--border-color,#2d2d44)]"
            >
              Incoming ({node.relations.incoming.length})
            </h4>
            {node.relations.incoming.length === 0 ? (
              <p className="px-4 py-3 text-sm text-gray-500">None.</p>
            ) : (
              <ul role="list">
                {node.relations.incoming.map((rel) => (
                  <RelationRow key={rel.edge_id} item={rel} />
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </main>
  )
}