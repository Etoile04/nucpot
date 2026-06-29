/**
 * BrowseFilterBar -- horizontal filter bar for the browse table.
 *
 * Provides filters for confidence, phase, temperature range, and staging status.
 */

"use client"

import { Select, Slider, Button, Space, Row, Col } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import {
  CONFIDENCE_LABELS,
  CANONICAL_PHASES,
  STAGING_STATUS_LABELS,
} from "@/lib/v4-extraction/constants"
import type { Confidence, StagingStatus, V4BrowseParams } from "@/lib/v4-extraction/types"

// ─── Props ──────────────────────────────────────────────────────────

interface BrowseFilterBarProps {
  filters: V4BrowseParams
  onFilterChange: (filters: V4BrowseParams) => void
}

// ─── Options ────────────────────────────────────────────────────────

const CONFIDENCE_OPTIONS = [
  { value: "high", label: CONFIDENCE_LABELS.high },
  { value: "medium", label: CONFIDENCE_LABELS.medium },
  { value: "low", label: CONFIDENCE_LABELS.low },
]

const STAGING_STATUS_OPTIONS = [
  { value: "pending", label: STAGING_STATUS_LABELS.pending },
  { value: "approved", label: STAGING_STATUS_LABELS.approved },
  { value: "rejected", label: STAGING_STATUS_LABELS.rejected },
  { value: "promoted", label: STAGING_STATUS_LABELS.promoted },
]

// ─── Component ──────────────────────────────────────────────────────

export default function BrowseFilterBar({
  filters,
  onFilterChange,
}: BrowseFilterBarProps) {
  const handleConfidenceChange = (value: string) => {
    onFilterChange({
      ...filters,
      confidence: (value || undefined) as Confidence | undefined,
    })
  }

  const handlePhaseChange = (value: string) => {
    onFilterChange({
      ...filters,
      phase: value || undefined,
    })
  }

  const handleTemperatureChange = (value: number | number[]) => {
    if (Array.isArray(value)) {
      onFilterChange({
        ...filters,
        temperature_min: value[0],
        temperature_max: value[1],
      })
    }
  }

  const handleStagingStatusChange = (value: string) => {
    onFilterChange({
      ...filters,
      staging_status: (value || undefined) as StagingStatus | undefined,
    })
  }

  const handleReset = () => {
    onFilterChange({
      page: 1,
      limit: 50,
    })
  }

  const tempMin = filters.temperature_min ?? 0
  const tempMax = filters.temperature_max ?? 3000

  return (
    <Row gutter={[16, 12]} align="middle" style={{ marginBottom: 12 }}>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 160 }}>
          <span style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            置信度 / Confidence
          </span>
          <Select
            placeholder="全部"
            allowClear
            size="small"
            value={filters.confidence ?? undefined}
            onChange={handleConfidenceChange}
            style={{ width: 160 }}
            options={CONFIDENCE_OPTIONS}
          />
        </Space>
      </Col>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 160 }}>
          <span style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            相 / Phase
          </span>
          <Select
            placeholder="全部"
            allowClear
            showSearch
            size="small"
            value={filters.phase ?? undefined}
            onChange={handlePhaseChange}
            style={{ width: 160 }}
            options={CANONICAL_PHASES.map((phase) => ({
              value: phase,
              label: phase,
            }))}
          />
        </Space>
      </Col>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 240 }}>
          <span style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            温度范围 / Temperature (K)
          </span>
          <Slider
            range
            min={0}
            max={3000}
            step={50}
            value={[tempMin, tempMax]}
            onChange={handleTemperatureChange}
            style={{ width: 240, margin: "4px 0" }}
          />
        </Space>
      </Col>
      <Col>
        <Space size={4} direction="vertical" style={{ width: 160 }}>
          <span style={{ fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
            暂存状态 / Staging Status
          </span>
          <Select
            placeholder="全部"
            allowClear
            size="small"
            value={filters.staging_status ?? undefined}
            onChange={handleStagingStatusChange}
            style={{ width: 160 }}
            options={STAGING_STATUS_OPTIONS}
          />
        </Space>
      </Col>
      <Col>
        <Button
          icon={<ReloadOutlined />}
          size="small"
          onClick={handleReset}
          style={{ marginTop: 18 }}
        >
          重置 / Reset
        </Button>
      </Col>
    </Row>
  )
}
