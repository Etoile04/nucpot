"use client"

import { useState, useEffect, useCallback } from "react"
import {
  Input,
  Table,
  Tag,
  Card,
  Empty,
  Spin,
  Typography,
  Space,
  Checkbox,
} from "antd"
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
  { label: "EAM", value: "EAM" },
  { label: "MEAM", value: "MEAM" },
  { label: "ADP", value: "ADP" },
  { label: "Morse", value: "Morse" },
  { label: "Finnis-Sinclair", value: "FS" },
]

export function PotentialSelectorStep({
  formData,
  onUpdateField,
}: PotentialSelectorStepProps) {
  const [searchQuery, setSearchQuery] = useState("")
  const [typeFilters, setTypeFilters] = useState<string[]>([])
  const [results, setResults] = useState<PotentialSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)

  const fetchPotentials = useCallback(async () => {
    setLoading(true)
    try {
      const typeFilter =
        typeFilters.length > 0 ? typeFilters.join(",") : undefined
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
  }, [searchQuery, typeFilters, page])

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

  const handleTypeFilterChange = (checkedValues: string[]) => {
    setTypeFilters(checkedValues)
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
      {/* Search bar */}
      <Input.Search
        placeholder="搜索势函数名称/元素..."
        value={searchQuery}
        onSearch={handleSearchChange}
        onChange={(e) => handleSearchChange(e.target.value)}
        allowClear
        enterButton
        style={{ width: "100%" }}
      />

      {/* Type filter using Checkbox.Group (vertical layout per NFM-378) */}
      <Checkbox.Group
        options={POTENTIAL_TYPE_OPTIONS}
        value={typeFilters}
        onChange={handleTypeFilterChange}
      />

      {/* Selected potential card */}
      {formData.selectedPotential && (
        <Card
          size="small"
          title="已选势函数"
          style={{
            background: "var(--wizard-border-color)",
            borderColor: "var(--step-finish-color)",
          }}
        >
          <Space wrap>
            <Text strong>{formData.selectedPotential.name}</Text>
            <Tag color="blue">{formData.selectedPotential.type}</Tag>
            {formData.selectedPotential.elements.map((el) => (
              <Tag key={el}>{el}</Tag>
            ))}
            {formData.selectedPotential.description && (
              <Text
                type="secondary"
                style={{ fontSize: "var(--hint-text-size)" }}
              >
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
          locale={{
            emptyText: <Empty description="未找到匹配的势函数" />,
          }}
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
