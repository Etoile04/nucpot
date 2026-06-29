/**
 * ValidationProgress -- progress bar and review stat badges.
 *
 * Shows a gradient progress bar (blue to green) with format "3 / 12"
 * and four stat badges below: approved, rejected, skipped, pending.
 */

"use client"

import { Progress, Row, Col, Tag } from "antd"

// ─── Props ────────────────────────────────────────────────────────

interface ValidationProgressProps {
  current: number
  total: number
  approved: number
  rejected: number
  skipped: number
}

// ─── Component ────────────────────────────────────────────────────

export default function ValidationProgress({
  current,
  total,
  approved,
  rejected,
  skipped,
}: ValidationProgressProps) {
  const percent = total > 0 ? Math.round((current / total) * 100) : 0
  const pending = total - approved - rejected - skipped

  return (
    <div style={{ marginBottom: 16 }}>
      <Progress
        percent={percent}
        format={() => `${current} / ${total}`}
        strokeColor={{
          "0%": "#1890ff",
          "100%": "#52c41a",
        }}
        size="small"
      />
      <Row gutter={[8, 4]} style={{ marginTop: 8 }}>
        <Col>
          <Tag color="green">
            ✓ 已批准:{approved}
          </Tag>
        </Col>
        <Col>
          <Tag color="red">
            ✕ 已拒绝:{rejected}
          </Tag>
        </Col>
        <Col>
          <Tag color="default">
            ⏭ 跳过:{skipped}
          </Tag>
        </Col>
        <Col>
          <Tag color="blue">
            ⏳ 待审核:{pending}
          </Tag>
        </Col>
      </Row>
    </div>
  )
}
