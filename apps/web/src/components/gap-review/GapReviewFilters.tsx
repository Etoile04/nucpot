/**
 * GapReviewFilters -- filter bar for the Gap Review queue.
 *
 * 5 filters: confidence_min, confidence_max, entity_type, source_doc, decision_status.
 * Follows the BrowseFilterBar pattern (Ant Design Row/Col/Select).
 *
 * Spec: NFM-3704
 */

"use client"

import { useCallback } from 'react'
import { Select, Input, Button, Row, Col, InputNumber, Space } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import {
  DECISION_STATUS_OPTIONS,
  ENTITY_TYPE_OPTIONS,
  DEFAULT_FILTERS,
} from '@/lib/reference-gaps/gap-candidates'
import type { GapCandidateFilters } from '@/lib/reference-gaps/gap-candidates'

interface GapReviewFiltersProps {
  readonly filters: GapCandidateFilters
  readonly onFilterChange: (filters: GapCandidateFilters) => void
}

export function GapReviewFilters({
  filters,
  onFilterChange,
}: GapReviewFiltersProps) {
  const handleConfidenceMinChange = useCallback(
    (value: number | null) => {
      onFilterChange({ ...filters, confidence_min: value ?? undefined })
    },
    [filters, onFilterChange],
  )

  const handleConfidenceMaxChange = useCallback(
    (value: number | null) => {
      onFilterChange({ ...filters, confidence_max: value ?? undefined })
    },
    [filters, onFilterChange],
  )

  const handleEntityTypeChange = useCallback(
    (value: string) => {
      onFilterChange({ ...filters, entity_type: value || undefined })
    },
    [filters, onFilterChange],
  )

  const handleSourceDocChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      onFilterChange({ ...filters, source_doc: e.target.value || undefined })
    },
    [filters, onFilterChange],
  )

  const handleDecisionStatusChange = useCallback(
    (value: string) => {
      onFilterChange({ ...filters, decision_status: value || undefined })
    },
    [filters, onFilterChange],
  )

  const handleReset = useCallback(() => {
    onFilterChange({ ...DEFAULT_FILTERS })
  }, [onFilterChange])

  return (
    <Row gutter={[16, 12]} align="middle" style={{ marginBottom: 16 }}>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 130 }}>
          <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>Min Confidence</span>
          <InputNumber placeholder="Min Confidence" min={0} max={1} step={0.1} size="small" value={filters.confidence_min} onChange={handleConfidenceMinChange} style={{ width: 130 }} />
        </Space>
      </Col>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 130 }}>
          <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>Max Confidence</span>
          <InputNumber placeholder="Max Confidence" min={0} max={1} step={0.1} size="small" value={filters.confidence_max} onChange={handleConfidenceMaxChange} style={{ width: 130 }} />
        </Space>
      </Col>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 140 }}>
          <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>Entity Type</span>
          <Select placeholder="Entity Type" allowClear showSearch size="small" value={filters.entity_type ?? undefined} onChange={handleEntityTypeChange} style={{ width: 140 }} options={ENTITY_TYPE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))} />
        </Space>
      </Col>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 160 }}>
          <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>Source Doc</span>
          <Input placeholder="Source Doc" allowClear size="small" value={filters.source_doc ?? ''} onChange={handleSourceDocChange} style={{ width: 160 }} />
        </Space>
      </Col>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 130 }}>
          <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>Decision Status</span>
          <Select placeholder="Decision Status" allowClear size="small" value={filters.decision_status ?? undefined} onChange={handleDecisionStatusChange} style={{ width: 130 }} options={DECISION_STATUS_OPTIONS.map((o) => ({ value: o.value, label: o.label }))} />
        </Space>
      </Col>
      <Col>
        <Button icon={<ReloadOutlined />} size="small" onClick={handleReset} style={{ marginTop: 18 }}>Reset</Button>
      </Col>
    </Row>
  )
}
