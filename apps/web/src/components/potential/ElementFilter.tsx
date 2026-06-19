"use client"

import { Select } from "antd"
import { ELEMENT_OPTIONS } from "./element-options"

interface ElementFilterProps {
  readonly value: string[]
  readonly onChange: (v: string[]) => void
}

export function ElementFilter({ value, onChange }: ElementFilterProps) {
  return (
    <Select
      mode="multiple"
      placeholder="选择元素"
      value={value}
      onChange={onChange}
      options={ELEMENT_OPTIONS}
      allowClear
      showSearch
      className="min-w-[180px]"
      maxTagCount={5}
      filterOption={(input, option) =>
        ((option?.label as string) ?? "").toLowerCase().includes(input.toLowerCase())
      }
    />
  )
}
