"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
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

const { Title, Text } = Typography

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
  return (
    <Result
      status="warning"
      title="节点未找到"
      subTitle={`材料 "${materialId}" 在知识图谱中未找到。该材料可能尚未被提取到知识图谱中。`}
      extra={[
        <Link key="back" href={`/materials/${materialId}/properties`}>
          <Button type="primary">返回材料属性</Button>
        </Link>,
        <Link key="browse" href="/browse">
          <Button>浏览材料</Button>
        </Link>,
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
        focalId: response.focal.id,
        errorMessage: null,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : "未知错误"

      if (message.includes("404") || message.includes("not found")) {
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

      if (node.type === "material") {
        router.push(`/materials/${node.id}/properties`)
      } else {
        router.push(`/materials/${node.id}/graph`)
      }
    },
    [router, state.focalId],
  )

  const graphHeight = useMemo(() => "calc(100vh - 220px)", [])

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
          height={graphHeight}
          showControls={true}
          initialZoom={1}
          className="material-graph-canvas"
        />
      )}
    </main>
  )
}
