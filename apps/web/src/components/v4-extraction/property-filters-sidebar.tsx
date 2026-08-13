"use client"

import { Checkbox, Input, Select, Space, Button, Card } from "antd"
import { ReloadOutlined } from "@ant-design/icons"
import { PROPERTY_CATEGORIES } from "@/lib/v4-extraction/constants"

interface FilterState {
  confidence?: string[]
  category?: string
  search?: string
}

interface PropertyFiltersSidebarProps {
  filters: FilterState
  onFilterChange: (filters: FilterState) => void
}

const CONFIDENCE_OPTIONS = [
  { label: "高 / High", value: "high" },
  { label: "中 / Medium", value: "medium" },
  { label: "低 / Low", value: "low" },
]

export default function PropertyFiltersSidebar({
  filters,
  onFilterChange,
}: PropertyFiltersSidebarProps) {
  const handleConfidenceChange = (checkedValues: string[]) => {
    onFilterChange({
      ...filters,
      confidence: checkedValues.length > 0 ? checkedValues : undefined,
    })
  }

  const handleCategoryChange = (value: string | undefined) => {
    onFilterChange({
      ...filters,
      category: value || undefined,
    })
  }

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onFilterChange({
      ...filters,
      search: e.target.value || undefined,
    })
  }

  const handleReset = () => {
    onFilterChange({})
  }

  return (
    <Card
      title="筛选条件 / Filters"
      size="small"
      style={{ height: "fit-content" }}
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {/* Property name search */}
        <div>
          <div style={{ marginBottom: 4, fontWeight: 500, fontSize: 13 }}>
            属性名称 / Property
          </div>
          <Input
            placeholder="搜索属性名称..."
            allowClear
            value={filters.search ?? ""}
            onChange={handleSearchChange}
          />
        </div>

        {/* Confidence checkboxes */}
        <div>
          <div style={{ marginBottom: 4, fontWeight: 500, fontSize: 13 }}>
            置信度 / Confidence
          </div>
          <Checkbox.Group
            value={filters.confidence ?? []}
            onChange={handleConfidenceChange}
            style={{ display: "flex", flexDirection: "column" }}
          >
            {CONFIDENCE_OPTIONS.map((opt) => (
              <Checkbox key={opt.value} value={opt.value}>
                {opt.label}
              </Checkbox>
            ))}
          </Checkbox.Group>
        </div>

        {/* Category dropdown */}
        <div>
          <div style={{ marginBottom: 4, fontWeight: 500, fontSize: 13 }}>
            类别 / Category
          </div>
          <Select
            placeholder="选择类别..."
            allowClear
            value={filters.category ?? undefined}
            onChange={handleCategoryChange}
            style={{ width: "100%" }}
            options={PROPERTY_CATEGORIES.map((cat) => ({
              value: cat.value,
              label: cat.label,
            }))}
          />
        </div>

        {/* Reset button */}
        <Button
          icon={<ReloadOutlined />}
          onClick={handleReset}
          block
        >
          重置 / Reset
        </Button>
      </Space>
    </Card>
  )
}
