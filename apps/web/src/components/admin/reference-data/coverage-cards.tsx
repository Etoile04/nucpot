/** Coverage summary cards component. */

import { Card, Col, Row, Statistic } from "antd"
import type { ReferenceGapsSummaryResponse } from "@/lib/reference-gaps/types"

interface CoverageCardsProps {
  data: ReferenceGapsSummaryResponse
  loading?: boolean
}

export function CoverageCards({ data, loading }: CoverageCardsProps) {
  return (
    <Row gutter={16}>
      <Col xs={12} sm={6}>
        <Card loading={loading}>
          <Statistic
            title="目标总数"
            value={data.total_target_tuples}
            valueStyle={{ color: "#1890ff" }}
          />
        </Card>
      </Col>
      <Col xs={12} sm={6}>
        <Card loading={loading}>
          <Statistic
            title="已覆盖"
            value={data.covered}
            valueStyle={{ color: "#52c41a" }}
          />
        </Card>
      </Col>
      <Col xs={12} sm={6}>
        <Card loading={loading}>
          <Statistic
            title="缺口"
            value={data.gaps}
            valueStyle={{ color: "#ff4d4f" }}
          />
        </Card>
      </Col>
      <Col xs={12} sm={6}>
        <Card loading={loading}>
          <Statistic
            title="覆盖率"
            value={data.coverage_percent}
            precision={1}
            suffix="%"
            valueStyle={{
              color: data.coverage_percent >= 80 ? "#52c41a" : "#faad14",
            }}
          />
        </Card>
      </Col>
    </Row>
  )
}
