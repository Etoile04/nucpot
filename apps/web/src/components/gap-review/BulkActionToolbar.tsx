
"use client"

import { useCallback, useMemo, useState } from 'react'
import { Button, Space, Typography, Tooltip } from 'antd'
import { CheckOutlined, CloseOutlined, ClockCircleOutlined } from '@ant-design/icons'

import type { BulkActionToolbarProps, GapCandidate, GapDecision } from '@/lib/gap-decisions/types'
import {
  buildDecisionPayload,
  filterByConfidence,
  submitBulkDecisions,
} from '@/lib/gap-decisions/bulk-decisions-api'

export function BulkActionToolbar({
  selectedItems,
  confidenceThreshold,
  onSuccess,
  onError,
  loading: externalLoading,
}: BulkActionToolbarProps) {
  const [loading, setLoading] = useState(false)
  const isLoading = externalLoading ?? loading

  const aboveThreshold = useMemo(
    () => filterByConfidence(selectedItems, confidenceThreshold),
    [selectedItems, confidenceThreshold],
  )

  const acceptCount = aboveThreshold.length
  const totalCount = selectedItems.length

  const executeBulk = useCallback(
    async (candidates: ReadonlyArray<GapCandidate>, decision: GapDecision) => {
      if (candidates.length === 0) return
      setLoading(true)
      try {
        const payload = buildDecisionPayload(candidates, decision)
        await submitBulkDecisions(payload)
        onSuccess()
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Bulk operation failed'
        onError(msg)
      } finally {
        setLoading(false)
      }
    },
    [onSuccess, onError],
  )

  const handleAcceptAboveThreshold = useCallback(
    () => executeBulk(aboveThreshold, 'accepted'),
    [executeBulk, aboveThreshold],
  )

  const handleReject = useCallback(
    () => executeBulk(selectedItems, 'rejected'),
    [executeBulk, selectedItems],
  )

  const handleDefer = useCallback(
    () => executeBulk(selectedItems, 'deferred'),
    [executeBulk, selectedItems],
  )

  if (totalCount === 0) return null

  return (
    <div
      role="toolbar"
      aria-label="Bulk actions"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 16px',
        background: 'var(--color-bg-container, #fff)',
        borderBottom: '1px solid var(--color-border-secondary, #f0f0f0)',
        position: 'sticky',
        top: 0,
        zIndex: 10,
      }}
    >
      <Typography.Text strong style={{ whiteSpace: 'nowrap' }}>
        {totalCount} selected
      </Typography.Text>

      <Space size="small">
        <Tooltip title={acceptCount + ' items above threshold'}>
          <Button
            type="primary"
            icon={<CheckOutlined />}
            loading={isLoading}
            onClick={handleAcceptAboveThreshold}
            disabled={acceptCount === 0}
            data-testid="bulk-accept"
          >
            Accept {">="} {confidenceThreshold} ({acceptCount})
          </Button>
        </Tooltip>

        <Button
          danger
          icon={<CloseOutlined />}
          loading={isLoading}
          onClick={handleReject}
          data-testid="bulk-reject"
        >
          Reject ({totalCount})
        </Button>

        <Button
          icon={<ClockCircleOutlined />}
          loading={isLoading}
          onClick={handleDefer}
          data-testid="bulk-defer"
        >
          Defer ({totalCount})
        </Button>
      </Space>
    </div>
  )
}
