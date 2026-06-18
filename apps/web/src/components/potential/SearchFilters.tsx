"use client"

import { Select, Input, Space } from "antd"
import { ElementFilter } from "./ElementFilter"

const TYPE_OPTIONS = [
  { label: "EAM", value: "EAM" },
  { label: "MEAM", value: "MEAM" },
  { label: "MTP", value: "MTP" },
  { label: "ACE", value: "ACE" },
]

interface SearchFiltersProps {
  readonly type: string | undefined
  readonly onTypeChange: (v: string | undefined) => void
  readonly elements: string[]
  readonly onElementsChange: (v: string[]) => void
  readonly query: string
  readonly onQueryChange: (v: string) => void
}

export function SearchFilters({
  type,
  onTypeChange,
  elements,
  onElementsChange,
  query,
  onQueryChange,
}: SearchFiltersProps) {
  return (
    <Space wrap>
      <Select
        placeholder="类型"
        value={type}
        onChange={onTypeChange}
        options={TYPE_OPTIONS}
        allowClear
        style={{ minWidth: 120 }}
      />
      <ElementFilter value={elements} onChange={onElementsChange} />
      <Input.Search
        placeholder="搜索势函数"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        style={{ minWidth: 200 }}
        allowClear
      />
    </Space>
  )
}
