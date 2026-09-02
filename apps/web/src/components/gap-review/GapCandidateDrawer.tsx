/**
 * GapCandidateDrawer — right-slide panel showing candidate details with
 * highlighted source passage, entity info, and accept/reject/defer actions.
 *
 * Uses Ant Design Drawer for built-in focus trap, Escape dismiss,
 * overlay click, and mobile bottom-sheet support.
 * Spec: NFM-3706
 */

'use client'

import { useCallback, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Drawer, Descriptions, Spin, Tag, Divider, Space, Empty } from 'antd'
import { EntityMatchHighlight } from './EntityMatchHighlight'
import { getCandidateHistory, postDecision } from '@/lib/reference-gaps/api'
import type {
  AuditEntry,
  DecisionKind,
  GapCandidate,
} from '@/lib/reference-gaps/types'

// ── Types ──────────────────────────────────────────────────────────

interface GapCandidateDrawerProps {
  readonly candidate: GapCandidate | null
  readonly open: boolean
  readonly onClose: () => void
  /** Called after a successful decision so the parent list can update. */
  readonly onDecision?: (candidateId: string, decision: DecisionKind) => void
}

// ── Constants ──────────────────────────────────────────────────────

const DECISION_CONFIG: ReadonlyArray<{
  readonly kind: DecisionKind
  readonly label: string
  readonly buttonType: 'primary' | 'default' | 'dashed'
  readonly danger?: boolean
}> = [
  { kind: 'accepted', label: '采纳', buttonType: 'primary' },
  { kind: 'rejected', label: '拒绝', buttonType: 'default', danger: true },
  { kind: 'deferred', label: '暂缓', buttonType: 'dashed' },
] as const

const CONFIDENCE_COLOR = (c: number): string => {
  if (c >= 0.8) return 'green'
  if (c >= 0.5) return 'orange'
  return 'red'
}

const DECISION_LABEL: Record<DecisionKind, string> = {
  accepted: '采纳',
  rejected: '拒绝',
  deferred: '暂缓',
}

// ── Sub-components ────────────────────────────────────────────────

function PriorDecisions({
  candidateId,
}: {
  readonly candidateId: string
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['gap-candidate-history', candidateId],
    queryFn: () => getCandidateHistory(candidateId),
    staleTime: 60_000,
  })

  if (isLoading) {
    return (
      <div className="flex justify-center py-4">
        <Spin size="small" />
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-xs text-gray-500 py-2">
        无法加载历史记录
      </p>
    )
  }

  const decisions = data?.decisions ?? []

  if (decisions.length === 0) {
    return <Empty description="暂无决策记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  return (
    <ul className="space-y-2">
      {decisions.map((entry: AuditEntry) => (
        <li
          key={entry.id}
          className="bg-gray-900/50 rounded border border-gray-700/50 p-3 space-y-1"
        >
          <div className="flex items-center justify-between">
            <Tag color={entry.decision === 'accepted' ? 'green' : entry.decision === 'rejected' ? 'red' : 'orange'}>
              {DECISION_LABEL[entry.decision]}
            </Tag>
            <span className="text-xs text-gray-500">{entry.reviewer_name}</span>
          </div>
          <p className="text-xs text-gray-400">{entry.source_document}</p>
          <p className="text-xs text-gray-600">{entry.decided_at}</p>
        </li>
      ))}
    </ul>
  )
}

// ── Main Component ────────────────────────────────────────────────

export function GapCandidateDrawer({
  candidate,
  open,
  onClose,
  onDecision,
}: GapCandidateDrawerProps) {
  const queryClient = useQueryClient()
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const [optimisticDecision, setOptimisticDecision] = useState<DecisionKind | null>(null)
  const [rollbackError, setRollbackError] = useState(false)

  const mutation = useMutation({
    mutationFn: postDecision,
    onMutate: async (variables) => {
      // Snapshot current state for rollback
      void queryClient.getQueryData(['gap-candidate-history', variables.candidate_id])
      setOptimisticDecision(variables.decision)
      setRollbackError(false)
    },
    onSuccess: (_data, variables) => {
      // Invalidate history so it refetches
      void queryClient.invalidateQueries({
        queryKey: ['gap-candidate-history', variables.candidate_id],
      })
      setOptimisticDecision(null)
      onDecision?.(variables.candidate_id, variables.decision)
      onClose()
    },
    onError: () => {
      // Rollback optimistic state
      setRollbackError(true)
      setOptimisticDecision(null)
    },
  })

  const handleDecision = useCallback(
    (kind: DecisionKind) => {
      if (!candidate) return
      mutation.mutate({ candidate_id: candidate.id, decision: kind })
    },
    [candidate, mutation],
  )

  const handleAfterOpenChange = useCallback(
    (isOpen: boolean) => {
      if (!isOpen && triggerRef.current) {
        // Return focus to the trigger element on close
        triggerRef.current.focus()
        triggerRef.current = null
      }
    },
    [],
  )

  if (!candidate) return null

  return (
    <Drawer
      title={
        <span className="font-semibold">{candidate.entity_name}</span>
      }
      width={480}
      open={open}
      onClose={onClose}
      afterOpenChange={handleAfterOpenChange}
      placement="right"
      footer={
        <div className="flex items-center justify-between">
          {rollbackError && (
            <span className="text-xs text-red-400">操作失败，请重试</span>
          )}
          <Space>
            {DECISION_CONFIG.map((cfg) => (
              <Button
                key={cfg.kind}
                type={cfg.buttonType}
                danger={cfg.danger}
                loading={
                  mutation.isPending && optimisticDecision === cfg.kind
                }
                disabled={mutation.isPending}
                onClick={() => handleDecision(cfg.kind)}
              >
                {cfg.label}
              </Button>
            ))}
          </Space>
        </div>
      }
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {/* Candidate Details */}
        <div>
          <Divider orientation="start" plain style={{ margin: 0, fontSize: 13 }}>
            候选实体信息
          </Divider>
          <Descriptions column={2} size="small" style={{ marginTop: 8 }}>
            <Descriptions.Item label="实体名称" span={2}>
              <span className="font-semibold">{candidate.entity_name}</span>
            </Descriptions.Item>
            <Descriptions.Item label="类型">{candidate.entity_type}</Descriptions.Item>
            <Descriptions.Item label="置信度">
              <Tag color={CONFIDENCE_COLOR(candidate.confidence)}>
                {(candidate.confidence * 100).toFixed(0)}%
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="来源文档" span={2}>
              <span className="text-xs text-gray-400">{candidate.source_document}</span>
            </Descriptions.Item>
          </Descriptions>
        </div>

        {/* Source Passage with Highlights */}
        <div>
          <Divider orientation="start" plain style={{ margin: 0, fontSize: 13 }}>
            来源段落
          </Divider>
          <div
            className="mt-2 p-3 rounded-lg bg-gray-900/50 border border-gray-700/50 text-sm leading-relaxed text-gray-300"
          >
            <EntityMatchHighlight
              text={candidate.source_passage}
              matchSpans={candidate.match_spans}
            />
          </div>
        </div>

        {/* Suggested Properties */}
        {candidate.suggested_properties.length > 0 && (
          <div>
            <Divider orientation="start" plain style={{ margin: 0, fontSize: 13 }}>
              建议属性
            </Divider>
            <div className="mt-2 space-y-1">
              {candidate.suggested_properties.map((props, i) => (
                <div
                  key={i}
                  className="flex flex-wrap gap-2 bg-gray-900/50 rounded border border-gray-700/50 p-2"
                >
                  {Object.entries(props).map(([k, v]) => (
                    <Tag key={k}>
                      {k}: {v}
                    </Tag>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Prior Decisions */}
        <div>
          <Divider orientation="start" plain style={{ margin: 0, fontSize: 13 }}>
            历史决策
          </Divider>
          <div className="mt-2">
            <PriorDecisions candidateId={candidate.id} />
          </div>
        </div>
      </Space>
    </Drawer>
  )
}
