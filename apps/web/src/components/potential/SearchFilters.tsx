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
  readonly allElements?: readonly string[]
  readonly query: string
  readonly onQueryChange: (v: string) => void
}

export function SearchFilters({
  type,
  onTypeChange,
  elements,
  onElementsChange,
  allElements,
  query,
  onQueryChange,
}: SearchFiltersProps) {
  const handleToggle = (el: string) => {
    if (elements.includes(el)) {
      onElementsChange(elements.filter((e) => e !== el))
    } else {
      onElementsChange([...elements, el])
    }
  }

  return (
    <Space wrap>
      <Select
        placeholder="类型"
        value={type}
        onChange={onTypeChange}
        options={TYPE_OPTIONS}
        allowClear
        className="min-w-[120px]"
      />
      <ElementFilter
        allElements={allElements ?? elements}
        selected={elements}
        onToggle={handleToggle}
      />
      <Input.Search
        placeholder="搜索势函数"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        className="min-w-[200px]"
        allowClear
      />
    </Space>
  )
}
