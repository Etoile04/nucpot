/** Gap Dashboard admin page. */

"use client"

import { useState, useEffect } from "react"
import { Card, Space, Spin, Alert, message } from "antd"
import { CoverageCards } from "@/components/admin/reference-data/coverage-cards"
import { GapHeatmap } from "@/components/admin/reference-data/gap-heatmap"
import { getGapsSummary } from "@/lib/reference-gaps/api"
import type { ReferenceGapsSummaryResponse } from "@/lib/reference-gaps/types"

export default function GapDashboardPage() {
  const [loading, setLoading] = useState(true)
  const [data, setData] = useState<ReferenceGapsSummaryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const summary = await getGapsSummary()
      setData(summary)
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "加载数据失败"
      setError(errorMsg)
      message.error(errorMsg)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const handleFillSuccess = () => {
    message.success("缺口填充成功，正在刷新数据...")
    loadData()
  }

  if (error) {
    return (
      <div style={{ padding: "24px" }}>
        <Alert
          type="error"
          message="加载失败"
          description={error}
          showIcon
          action={
            <a
              onClick={loadData}
              style={{ fontSize: "12px", cursor: "pointer" }}
            >
              重试
            </a>
          }
        />
      </div>
    )
  }

  return (
    <div style={{ padding: "24px" }}>
      <Spin spinning={loading && !data}>
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Card title="参考数据缺口管理">
            <p style={{ marginBottom: "16px", color: "#666" }}>
              监控核燃料与材料物性数据库参考数据的覆盖情况，识别并填充缺失数据。
            </p>
          </Card>

          {data && (
            <>
              <CoverageCards data={data} loading={loading} />
              <GapHeatmap
                data={data.by_system}
                loading={loading}
                onFillSuccess={handleFillSuccess}
              />
            </>
          )}
        </Space>
      </Spin>
    </div>
  )
}
