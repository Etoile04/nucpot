"use client"

import { Spin, Alert, Card, Statistic, Row, Col, Tabs, Empty } from "antd"
import {
  FileTextOutlined,
  PictureOutlined,
  TableOutlined,
} from "@ant-design/icons"
import type { V4FigureResult, V4TableResult } from "@/lib/v4-extraction/types"

import TextSection from "@/components/v4-extraction/text-section"
import FigureViewer from "@/components/v4-extraction/figure-viewer"
import TableViewer from "@/components/v4-extraction/table-viewer"

interface ExtractionResultProps {
  readonly text?: string
  readonly figures: readonly V4FigureResult[]
  readonly tables: readonly V4TableResult[]
  readonly loading?: boolean
  readonly error?: Error | null
}

function getMultimodalSummary(
  figures: readonly V4FigureResult[],
  tables: readonly V4TableResult[],
) {
  const figureCount = figures.length
  const tableCount = tables.length
  const totalMultimodal = figureCount + tableCount

  if (totalMultimodal === 0) return null

  const avgConfidence =
    [
      ...figures.map((f) => f.extraction.plot_data?.confidence ?? 0),
      ...tables.map((t) => t.table_data.confidence),
    ].reduce((sum, c) => sum + c, 0) / totalMultimodal

  return {
    figureCount,
    tableCount,
    avgConfidence,
  }
}

export default function ExtractionResult({
  text,
  figures,
  tables,
  loading,
  error,
}: ExtractionResultProps) {
  const summary = getMultimodalSummary(figures, tables)

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: "4rem" }}>
        <Spin size="large" />
      </div>
    )
  }

  if (error) {
    return (
      <Alert
        type="error"
        message="加载多模态数据失败 / Failed to load multimodal data"
        description={error.message}
        showIcon
      />
    )
  }

  const hasText = Boolean(text)
  const hasFigures = figures.length > 0
  const hasTables = tables.length > 0
  const hasAnyMultimodal = hasText || hasFigures || hasTables

  if (!hasAnyMultimodal) {
    return (
      <Empty
        description="无多模态提取结果 / No multimodal extraction results"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    )
  }

  const tabItems = [
    {
      key: "text",
      label: (
        <span>
          <FileTextOutlined /> 文本 / Text
        </span>
      ),
      children: hasText ? (
        <TextSection text={text!} />
      ) : (
        <Empty
          description="无提取文本 / No extracted text"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ),
      disabled: !hasText,
    },
    {
      key: "figures",
      label: (
        <span>
          <PictureOutlined /> 图片 / Figures{" "}
          {hasFigures && (
            <span
              style={{
                marginLeft: 4,
                fontSize: 11,
                color: "#9ca3af",
              }}
            >
              ({figures.length})
            </span>
          )}
        </span>
      ),
      children: <FigureViewer figures={figures} />,
      disabled: !hasFigures,
    },
    {
      key: "tables",
      label: (
        <span>
          <TableOutlined /> 表格 / Tables{" "}
          {hasTables && (
            <span
              style={{
                marginLeft: 4,
                fontSize: 11,
                color: "#9ca3af",
              }}
            >
              ({tables.length})
            </span>
          )}
        </span>
      ),
      children: <TableViewer tables={tables} />,
      disabled: !hasTables,
    },
  ]

  // Default to the first enabled tab
  const defaultActiveKey = hasText ? "text" : hasFigures ? "figures" : "tables"

  return (
    <div>
      {/* Multimodal summary card */}
      {summary && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Row gutter={16} align="middle">
            <Col>
              <Statistic
                title="提取图片 / Figures"
                value={summary.figureCount}
                suffix="张"
                valueStyle={{ fontSize: 20 }}
              />
            </Col>
            <Col>
              <Statistic
                title="提取表格 / Tables"
                value={summary.tableCount}
                suffix="个"
                valueStyle={{ fontSize: 20 }}
              />
            </Col>
            <Col>
              <Statistic
                title="平均置信度 / Avg Confidence"
                value={(summary.avgConfidence * 100).toFixed(0)}
                suffix="%"
                valueStyle={{
                  fontSize: 20,
                  color:
                    summary.avgConfidence > 0.8
                      ? "var(--confidence-high, #10b981)"
                      : summary.avgConfidence >= 0.6
                        ? "var(--confidence-medium, #f59e0b)"
                        : "var(--confidence-low, #ef4444)",
                }}
              />
            </Col>
          </Row>
        </Card>
      )}

      {/* Tabbed content */}
      <Card>
        <Tabs
          defaultActiveKey={defaultActiveKey}
          items={tabItems}
          size="middle"
        />
      </Card>
    </div>
  )
}
