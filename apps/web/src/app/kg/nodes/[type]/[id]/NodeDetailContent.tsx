/**
 * KG Node Detail page — shows a single KG node's properties plus its
 * incoming/outgoing relations sidebar.
 *
 * Route: /kg/nodes/[type]/[id]
 * Spec: NFM-1099
 */

'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { Spin, Result, Button, Typography } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { ConfidenceBadge } from '@/components/shared/ConfidenceBadge'
import {
  fetchKgNode,
  fetchKgRelations,
  type KgNodeDetail,
  type RelationEdge,
} from '@/lib/kg-node-api'

const { Title, Text } = Typography

// ── State shape ──────────────────────────────────────────────────────

type FetchStatus = 'idle' | 'loading' | 'success' | 'not_found' | 'error'

interface NodeState {
  readonly status: FetchStatus
  readonly node: KgNodeDetail | null
  readonly nodeError: string | null
  readonly relations: readonly RelationEdge[]
  readonly relationsError: string | null
}

const INITIAL_STATE: NodeState = {
  status: 'idle',
  node: null,
  nodeError: null,
  relations: [],
  relationsError: null,
}

// ── Component ────────────────────────────────────────────────────────

export function NodeDetailContent() {
  const router = useRouter()
  const params = useParams<{ type: string; id: string }>()
  const nodeType = params?.type ?? ''
  const nodeId = params?.id ?? ''

  const [state, setState] = useState<NodeState>(INITIAL_STATE)
  const [retryCount, setRetryCount] = useState(0)

  // ── Fetch node detail ────────────────────────────────────────────
  useEffect(() => {
    if (!nodeType || !nodeId) return

    let cancelled = false
    setState((prev) => ({
      ...prev,
      status: 'loading',
      nodeError: null,
    }))

    fetchKgNode(nodeType, nodeId)
      .then((data) => {
        if (cancelled) return
        setState((prev) => ({
          ...prev,
          status: 'success',
          node: data,
          nodeError: null,
        }))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : 'Failed to load node'
        // Treat any 404 (from the API client's thrown message) as not_found
        const isNotFound = /not found/i.test(message) || /404/.test(message)
        setState((prev) => ({
          ...prev,
          status: isNotFound ? 'not_found' : 'error',
          nodeError: message,
        }))
      })

    return () => {
      cancelled = true
    }
  }, [nodeType, nodeId, retryCount])

  // ── Fetch relations once the node detail loaded ─────────────────
  useEffect(() => {
    if (state.status !== 'success' || !nodeId) return

    let cancelled = false
    fetchKgRelations(nodeId)
      .then((data) => {
        if (cancelled) return
        setState((prev) => ({
          ...prev,
          relations: data.items,
          relationsError: null,
        }))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : 'Failed to load relations'
        setState((prev) => ({
          ...prev,
          relationsError: message,
        }))
      })

    return () => {
      cancelled = true
    }
  }, [state.status, nodeId])

  // ── Derived ──────────────────────────────────────────────────────
  const propertyEntries = useMemo<readonly [string, string][]>(() => {
    if (!state.node) return []
    return Object.entries(state.node.properties).map(([k, v]) => [k, formatValue(v)])
  }, [state.node])

  // ── Handlers ─────────────────────────────────────────────────────
  const handleRelationClick = useCallback(
    (otherNode: { node_type: string; id: string }) => {
      router.push(`/kg/nodes/${otherNode.node_type}/${otherNode.id}`)
    },
    [router],
  )

  const handleBack = useCallback(() => {
    router.push('/kg/search')
  }, [router])

  const handleRetry = useCallback(() => {
    setState({ ...INITIAL_STATE })
    setRetryCount((prev) => prev + 1)
  }, [])

  // ── Render ───────────────────────────────────────────────────────
  if (state.status === 'loading' || state.status === 'idle') {
    return (
      <main className="max-w-[1200px] mx-auto px-6 py-8">
        <div
          className="flex justify-center items-center min-h-[400px]"
          data-testid="node-loading"
        >
          <Spin tip="Loading node..."><div /></Spin>
        </div>
      </main>
    )
  }

  if (state.status === 'not_found') {
    return (
      <main className="max-w-[1200px] mx-auto px-6 py-8">
        <Result
          status="404"
          title="Node not found"
          subTitle={
            state.nodeError ?? `No KG node ${nodeType}/${nodeId} exists.`
          }
          extra={
            <Button type="primary" onClick={handleBack}>
              Back to search
            </Button>
          }
        />
      </main>
    )
  }

  if (state.status === 'error') {
    return (
      <main className="max-w-[1200px] mx-auto px-6 py-8">
        <Result
          status="error"
          title="Failed to load node"
          subTitle={state.nodeError ?? 'Unknown error'}
          extra={
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              onClick={handleRetry}
            >
              Retry
            </Button>
          }
        />
      </main>
    )
  }

  // Success — `state.node` is guaranteed non-null here
  const node = state.node as KgNodeDetail

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      <Button
        type="text"
        icon={<ArrowLeftOutlined />}
        onClick={handleBack}
        className="!text-gray-300 hover:!text-white !p-0 mb-4"
      >
        Back to search
      </Button>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* ── Main: node detail ─────────────────────────────── */}
        <section className="lg:col-span-2">
          <div className="p-6 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)]">
            <div className="flex flex-wrap items-center gap-3 mb-4">
              <Title level={3} className="!m-0 !text-white">
                {node.label}
              </Title>
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border border-blue-500/30 bg-blue-500/20 text-blue-300">
                {node.node_type}
              </span>
              <ConfidenceBadge value={node.confidence} />
            </div>

            <Text type="secondary" className="!block mb-4 text-sm">
              ID:{' '}
              <code className="text-gray-400" data-testid="node-id">
                {truncateId(node.id)}
              </code>
            </Text>

            {node.source_id && (
              <Text type="secondary" className="!block mb-4 text-sm">
                Source: <span className="text-gray-300">{node.source_id}</span>
              </Text>
            )}

            {propertyEntries.length > 0 ? (
              <>
                <Title level={5} className="!text-white !mt-4 !mb-2">
                  Properties
                </Title>
                <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
                  {propertyEntries.map(([key, value]) => (
                    <div key={key} className="flex flex-col">
                      <dt className="text-xs text-gray-500">{key}</dt>
                      <dd className="text-sm text-gray-200 break-words">
                        {value}
                      </dd>
                    </div>
                  ))}
                </dl>
              </>
            ) : (
              <Text type="secondary" className="!block text-sm !mt-4">
                No properties recorded.
              </Text>
            )}
          </div>
        </section>

        {/* ── Sidebar: relations ────────────────────────────── */}
        <aside className="lg:col-span-1">
          <div className="p-4 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)]">
            <Title level={5} className="!text-white !m-0 !mb-3">
              Relations ({state.relations.length})
            </Title>

            {state.relationsError && (
              <Text type="danger" className="!block text-sm mb-2">
                {state.relationsError}
              </Text>
            )}

            {!state.relationsError && state.relations.length === 0 && (
              <Text type="secondary" className="!block text-sm">
                No relations recorded.
              </Text>
            )}

            <ul className="space-y-2">
              {state.relations.map((edge) => {
                // Determine the "other" endpoint relative to the current node.
                const other =
                  edge.source_node.id === node.id
                    ? edge.target_node
                    : edge.source_node
                return (
                  <li key={edge.id}>
                    <button
                      type="button"
                      onClick={() => handleRelationClick(other)}
                      className="group w-full text-left p-2 rounded hover:bg-[var(--bg-elevated-hover,#22223a)] transition-colors"
                      data-testid={`relation-${edge.id}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-mono text-blue-300 group-hover:text-blue-200">
                          {edge.relation_type}
                        </span>
                        <span className="text-sm text-gray-200 truncate">
                          {other.label}
                        </span>
                        <ConfidenceBadge value={edge.confidence} size="sm" />
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-gray-500">
                        <span>{other.node_type}</span>
                        <span>·</span>
                        <span className="font-mono">
                          {truncateId(other.id)}
                        </span>
                      </div>
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        </aside>
      </div>
    </main>
  )
}

// ── Helpers ─────────────────────────────────────────────────────────

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function truncateId(id: string): string {
  if (id.length <= 12) return id
  return `${id.slice(0, 8)}…`
}
