"use client"

import { useCallback, useEffect, useState } from "react"
import dynamic from "next/dynamic"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Typography, Skeleton, Result, Button } from "antd"
import type { GraphNode, GraphData } from "@/components/graph"
import {
  getKGGraph,
  transformGraphResponse,
  type KGGraphResponse,
} from "@/lib/kg-api"
// ApiHttpError was renamed to ApiError; not used for narrowing here.

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

type FetchStatus = "idle" | "loading" | "success" | "not_found" | "error"

interface ViewState {
  readonly status: FetchStatus
  readonly graphData: GraphData | null
  readonly focalId: string | null
  readonly errorMessage: string | null
}

const INITIAL_STATE: ViewState = {
  status: "idle",
  graphData: null,
  focalId: null,
  errorMessage: null,
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
      <Skeleton.Image
        active
        style={{ width: "100%", height: 500, borderRadius: 8 }}
      />
      <Skeleton active paragraph={{ rows: 2 }} />
    </div>
  )
}

function NotFoundState({ materialId }: { readonly materialId: string }) {
  // Buttons navigate programmatically — wrapping a <button> in Link's <a>
  // is invalid HTML (NFM-4308 ④).
  const router = useRouter()
  return (
    <Result
      status="warning"
      title="节点未找到"
      subTitle={`材料 "${materialId}" 在知识图谱中未找到。该材料可能尚未被提取到知识图谱中。`}
      extra={[
        <Button
          key="back"
          type="primary"
          onClick={() => router.push(`/materials/${materialId}/properties`)}
        >
          返回材料属性
        </Button>,
        <Button key="browse" onClick={() => router.push("/browse")}>
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
  const [state, setState] = useState<ViewState>(INITIAL_STATE)

  const fetchData = useCallback(async () => {
    setState((prev) => ({
      ...prev,
      status: "loading",
      graphData: null,
      focalId: null,
      errorMessage: null,
    }))

    try {
      const response: KGGraphResponse = await getKGGraph({
        nodeId: materialId,
        depth: 2,
      })

      const graphData = transformGraphResponse(response)

      setState({
        status: "success",
        graphData,
        focalId: response.focal?.id ?? null,
        errorMessage: null,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : "未知错误"

      // 404 (not-found in KG) is a normal flow, not a system error.
      // We match on the message text since the shared request() helper
      // throws a plain Error after reading the backend's `detail` field;
      // there is no typed HTTP status on the thrown object.
      if (err instanceof Error && /not found/i.test(message)) {
        setState((prev) => ({ ...prev, status: "not_found" }))
      } else {
        setState((prev) => ({
          ...prev,
          status: "error",
          errorMessage: message,
        }))
      }
    }
  }, [materialId])

  useEffect(() => {
    void fetchData()
  }, [fetchData])

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      if (node.id === state.focalId) return

      // Material nodes → properties page; everything else → generic KG node page.
      // Sending non-material nodes to another /graph route would create loops.
      if (node.type === "material") {
        router.push(`/materials/${node.id}/properties`)
      } else {
        router.push(`/kg/node/${node.id}`)
      }
    },
    [router, state.focalId],
  )

  return (
    <main className="max-w-[1400px] mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <Title level={2} className="!m-0 text-white">
            知识图谱
          </Title>
          <Text type="secondary">
            材料 ID：{materialId} — 邻域子图（深度 2）
          </Text>
        </div>
        <div className="flex gap-3">
          <Link
            href={`/materials/${materialId}/properties`}
            className="text-blue-400 hover:text-blue-300 text-sm"
          >
            材料属性
          </Link>
          <Link
            href="/browse"
            className="text-blue-400 hover:text-blue-300 text-sm"
          >
            返回浏览
          </Link>
        </div>
      </div>

      {/* Loading state */}
      {state.status === "loading" && <GraphLoadingSkeleton />}

      {/* Not found state */}
      {state.status === "not_found" && <NotFoundState materialId={materialId} />}

      {/* Error state */}
      {state.status === "error" && state.errorMessage && (
        <ErrorState message={state.errorMessage} onRetry={fetchData} />
      )}

      {/* Graph */}
      {state.status === "success" && state.graphData && (
        <GraphCanvas
          data={state.graphData}
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
