/**
 * LiveCounters -- 3 statistic cards showing extraction progress counts.
 *
 * Displays extracted / staged / rejected counts with color-coded icons.
 */

"use client"

import { Card, Col, Row, Statistic } from "antd"
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
} from "@ant-design/icons"

// ─── Props ────────────────────────────────────────────────────────

interface LiveCountersProps {
  extractedCount: number
  stagedCount: number
  rejectedCount: number
}

// ─── Component ────────────────────────────────────────────────────

export default function LiveCounters({
  extractedCount,
  stagedCount,
  rejectedCount,
}: LiveCountersProps) {
  return (
    <Row gutter={16}>
      <Col span={8}>
        <Card size="small" bordered>
          <Statistic
            title="已提取 / Extracted"
            value={extractedCount}
            valueStyle={{ color: "#1890ff" }}
            prefix={<FileSearchOutlined />}
          />
        </Card>
      </Col>
      <Col span={8}>
        <Card size="small" bordered>
          <Statistic
            title="已暂存 / Staged"
            value={stagedCount}
            valueStyle={{ color: "#52c41a" }}
            prefix={<CheckCircleOutlined />}
          />
        </Card>
      </Col>
      <Col span={8}>
        <Card size="small" bordered>
          <Statistic
            title="已拒绝 / Rejected"
            value={rejectedCount}
            valueStyle={{ color: "#ff4d4f" }}
            prefix={<CloseCircleOutlined />}
          />
        </Card>
      </Col>
    </Row>
  )
}
