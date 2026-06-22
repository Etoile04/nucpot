"use client"

import { useState, useEffect, useCallback } from "react"
import {
  Input,
  Select,
  Table,
  Tag,
  Card,
  Empty,
  Spin,
  Typography,
  Space,
} from "antd"
import { SearchOutlined } from "@ant-design/icons"
import { listPotentials } from "@/lib/potentials-api"
import type { PotentialSummary, ListParams } from "@/lib/potentials-api"
import type { WizardFormData } from "./wizard-types"

const { Text } = Typography

interface PotentialSelectorStepProps {
  formData: WizardFormData
  onUpdateField: (field: keyof WizardFormData, value: unknown) => void
}

const PAGE_SIZE = 10

const POTENTIAL_TYPE_OPTIONS = [
  { value: "EAM", label: "EAM" },
  { value: "MEAM", label: "MEAM" },
  { value: "ADP", label: "ADP" },
  { value: "Morse", label: "Morse" },
  { value: "FS", label: "Finnis-Sinclair" },
]

export function PotentialSelectorStep({
  formData,
  onUpdateField,
}: PotentialSelectorStepProps) {
  const [searchQuery, setSearchQuery] = useState("")
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined)
  const [results, setResults] = useState<PotentialSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)

  const fetchPotentials = useCallback(async () => {
    setLoading(true)
    try {
      const params: ListParams = {
        q: searchQuery || undefined,
        type: typeFilter,
        page,
        limit: PAGE_SIZE,
      }
      const data = await listPotentials(params)
      setResults(data.potentials)
      setTotal(data.total)
    } catch {
      setResults([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [searchQuery, typeFilter, page])

  useEffect(() => {
    const timer = setTimeout(fetchPotentials, 300)
    return () => clearTimeout(timer)
  }, [fetchPotentials])

  const handleSelect = (potential: PotentialSummary) => {
    onUpdateField("selectedPotential", potential)
    onUpdateField("elementSystem", potential.elements.join("-"))
  }

  const handleSearchChange = (value: string) => {
    setSearchQuery(value)
    setPage(1)
  }

  const handleTypeFilterChange = (value: string) => {
    setTypeFilter(value || undefined)
    setPage(1)
  }

  const columns = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      width: 220,
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: "类型",
      dataIndex: "type",
      key: "type",
      width: 100,
      render: (type: string) => <Tag color="blue">{type}</Tag>,
    },
    {
      title: "元素",
      dataIndex: "elements",
      key: "elements",
      render: (elements: string[]) => (
        <Space size={4} wrap>
          {elements.map((el) => (
            <Tag key={el}>{el}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      width: 70,
    },
    {
      title: "操作",
      key: "action",
      width: 100,
      render: (_: unknown, record: PotentialSummary) => {
        const isSelected = formData.selectedPotential?.id === record.id
        return (
          <a onClick={() => handleSelect(record)}>
            {isSelected ? "已选择 ✓" : "选择"}
          </a>
        )
      },
    },
  ]

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      {/* Search & Filter bar */}
      <Space wrap>
        <Input
          placeholder="搜索势函数名称或描述..."
          prefix={<SearchOutlined />}
          value={searchQuery}
          onChange={(e) => handleSearchChange(e.target.value)}
          style={{ width: 300 }}
          allowClear
        />
        <Select
          placeholder="类型筛选"
          value={typeFilter}
          onChange={handleTypeFilterChange}
          style={{ width: 150 }}
          allowClear
          options={POTENTIAL_TYPE_OPTIONS}
        />
      </Space>

      {/* Selected potential card */}
      {formData.selectedPotential && (
        <Card
          size="small"
          title="已选势函数"
          style={{ background: "#f6ffed", borderColor: "#b7eb8f" }}
        >
          <Space wrap>
            <Text strong>{formData.selectedPotential.name}</Text>
            <Tag color="blue">{formData.selectedPotential.type}</Tag>
            {formData.selectedPotential.elements.map((el) => (
              <Tag key={el}>{el}</Tag>
            ))}
            {formData.selectedPotential.description && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {formData.selectedPotential.description}
              </Text>
            )}
          </Space>
        </Card>
      )}

      {/* Results table */}
      <Spin spinning={loading}>
        <Table
          dataSource={results}
          columns={columns}
          rowKey="id"
          size="small"
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
            size: "small",
          }}
          locale={{ emptyText: <Empty description="暂无匹配势函数" /> }}
          rowClassName={(record) =>
            formData.selectedPotential?.id === record.id
              ? "ant-table-row-selected"
              : ""
          }
          scroll={{ x: 600 }}
        />
      </Spin>
    </Space>
  )
}
