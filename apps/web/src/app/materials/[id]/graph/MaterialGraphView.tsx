"use client"

/**
 * MaterialGraphView — depth-2 KG neighbourhood view for a single material.
 *
 * NFM-4449 Q4: the hand-rolled `useState<ViewState>` + `useEffect`
 * fetch machine is replaced by `useGraphView`, which collapses
 * loading/error/empty/fetch into a single 5-state `status` field.
 * The "not_found" branch is preserved as a sub-state of "empty"
 * (the queryFn returns `null` for 404s so the UI can render the
 * not-found Result without polluting the error path).
 */

import { useCallback } from "react"
import dynamic from "next/dynamic"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Typography, Skeleton, Result, Button } from "antd"
import type { GraphNode, GraphData } from "@/components/graph"
import { getKGGraph, transformGraphResponse, type KGGraphResponse } from "@/lib/kg-api"
import { useGraphView } from "@/hooks/useGraphView"

const { Title, Text } = Typography

// Static layout value — kept at module scope (avoids per-render allocation
// and the unnecessary `useMemo` wrapping a literal).
//
// NFM-4085 (B): the subtraction now accounts for the full chrome stack
// outside the canvas so the outer <main> (overflow-y-auto) doesn't show
// a small scrollbar on a typical viewport. Breakdown:
//   - Root Nav:            ~64px
//   - Root Footer:         ~88px
//   - Page py-8 (top+bot): 64px
//   - Page header (Title + Text + mb-6): ~80px
//   - Bottom buffer:       ~4px (avoids 1-2px scroll on tall windows)
// ----------------------------------------------------------------------------
//   - Total:              ~300px
//
// We deliberately leave the canvas a touch taller than the strict math
// says — a small overflow on tiny (e.g. 600px) windows is preferable to
// the canvas being too short to interact with on a 1080p display.
const GRAPH_HEIGHT = "calc(100vh - 300px)"

// ── Lazy-loaded GraphCanvas (minimises bundle impact) ──────────────────

const GraphCanvas = dynamic(
  () => import("@/components/graph").then((mod) => ({ default: mod.GraphCanvas })),
  {
    ssr: false,
    loading: () => <GraphLoadingSkeleton />,
  },
)

// ── Types ──────────────────────────────────────────────────────────────

interface MaterialGraphViewProps {
  readonly materialId: string
}

interface GraphFetchResult {
  /** Resolved graph data, or `null` for 404 (focal material not in KG). */
  readonly data: GraphData | null
  /** Focal-node id from the backend, when present. */
  readonly focalId: string | null
}

/**
 * NFM-4449 Q4: single queryFn for useGraphView. Translates a 404 from
 * the backend into a `data: null` result so the consumer can branch
 * on "not found" without the error path picking it up.
 */
async function fetchMaterialGraph(
  materialId: string,
): Promise<GraphFetchResult> {
  try {
    const response: KGGraphResponse = await getKGGraph({
      nodeId: materialId,
      depth: 2,
    })
    return {
      data: transformGraphResponse(response),
      focalId: response.focal?.id ?? null,
    }
  } catch (err) {
    if (err instanceof Error && /not found/i.test(err.message)) {
      return { data: null, focalId: null }
    }
    throw err
  }
}

// ── Sub-components ────────────────────────────────────────────────────

function GraphLoadingSkeleton() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        padding: 24,
      }}
      role="status"
      aria-busy="true"
    >
      <Skeleton active paragraph={{ rows: 1 }} />
      <Skeleton.Image active style={{ width: "100%", height: 500, borderRadius: 8 }} />
      <Skeleton active paragraph={{ rows: 2 }} />
    </div>
  )
}

function NotFoundState({ materialId }: { readonly materialId: string }) {
  // antd Button with href renders a single <a> styled as a button —
  // wrapping a <button> in Link's <a> is invalid HTML (NFM-4308 ④).
  return (
    <Result
      status="warning"
      title="节点未找到"
      subTitle={`材料 "${materialId}" 在知识图谱中未找到。该材料可能尚未被提取到知识图谱中。`}
      extra={[
        <Button key="back" type="primary" href={`/materials/${materialId}/properties`}>
          返回材料属性
        </Button>,
        <Button key="browse" href="/browse">
          浏览材料
        </Button>,
      ]}
    />
  )
}

function ErrorState({
  message,
  onRetry,
}: {
  readonly message: string
  readonly onRetry: () => void
}) {
  return (
    <Result
      status="error"
      title="加载失败"
      subTitle={message}
      extra={
        <Button type="primary" onClick={onRetry}>
          重试
        </Button>
      }
    />
  )
}

// ── Main Component ────────────────────────────────────────────────────

export function MaterialGraphView({ materialId }: MaterialGraphViewProps) {
  const router = useRouter()

  // NFM-4449 Q4 + Q6: TanStack Query via useGraphView. queryKey scopes
  // the cache per material so navigating between two materials doesn't
  // bleed stale data.
  const view = useGraphView<GraphFetchResult>({
    queryKey: ["material-graph", materialId] as const,
    queryFn: () => fetchMaterialGraph(materialId),
    // The graph data is focal-material-specific; don't reuse across
    // navigations. 5s is a small window where a tab switch + return
    // gets a free refetch instead of a forced refetch.
    staleTime: 5_000,
    // 404 → `data: null` is the "empty / not_found" sub-state; the
    // hook's default isEmpty would treat it as empty, which is what
    // we want — the view's main render branches on `data === null`.
    isEmpty: (result) => result.data === null,
  })

  const { data, status, error, retry } = view

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      if (data?.focalId && node.id === data.focalId) return

      // NFM-4445 — Material nodes route to the canonical materials.id
      // (server-supplied bridge), never the KG-node UUID.  Same-name
      // cohorts without a bridge (NFM-4093) fall through to the
      // generic KG-node route.  Non-material nodes always go to the
      // KG node page.
      if (node.type === "material" && node.materials_id) {
        router.push(`/materials/${node.materials_id}/properties`)
      } else {
        router.push(`/kg/node/${node.id}`)
      }
    },
    [router, data?.focalId],
  )

  // NFM-4449: status-aware render. Each branch maps cleanly onto the
  // 5-state machine. "fetch" / "retry" both render the canvas (the
  // background refetch doesn't block the UI); "loading" / "error" /
  // "empty" each have their own dedicated UI.
  return (
    <main className="max-w-[1400px] mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <Title level={2} className="!m-0 text-white">
            知识图谱
          </Title>
          <Text type="secondary">材料 ID：{materialId} — 邻域子图（深度 2）</Text>
        </div>
        <div className="flex gap-3">
          <Link
            href={`/materials/${materialId}/properties`}
            className="text-blue-400 hover:text-blue-300 text-sm"
          >
            材料属性
          </Link>
          <Link href="/browse" className="text-blue-400 hover:text-blue-300 text-sm">
            返回浏览
          </Link>
        </div>
      </div>

      {/* Loading state */}
      {(status === "loading" || status === "fetch") && status === "loading" && (
        <GraphLoadingSkeleton />
      )}

      {/* Not found state — useGraphView's isEmpty returns true for
          data: null, so this branch fires on a 404. */}
      {status === "empty" && <NotFoundState materialId={materialId} />}

      {/* Error state */}
      {status === "error" && error && (
        <ErrorState message={error.message} onRetry={retry} />
      )}

      {/* Graph — render for "retry" (settled, data is good) and for
          "fetch" (background refetch with stale data still on screen). */}
      {(status === "retry" || status === "fetch") &&
        data &&
        data.data && (
        <GraphCanvas
          data={data.data}
          onNodeClick={handleNodeClick}
          height={GRAPH_HEIGHT}
          showControls={true}
          initialZoom={1}
          className="material-graph-canvas"
        />
      )}
    </main>
  )
}
