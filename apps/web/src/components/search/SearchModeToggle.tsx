"use client"

import { Segmented } from "antd"

export type SearchMode = "text" | "semantic"

interface SearchModeToggleProps {
  readonly value: SearchMode
  readonly onChange: (mode: SearchMode) => void
}

const MODE_OPTIONS = [
  { label: "文本检索", value: "text" },
  { label: "语义 (RAG) 检索", value: "semantic" },
] as const

export function SearchModeToggle({ value, onChange }: SearchModeToggleProps) {
  return (
    <Segmented
      options={MODE_OPTIONS.map((o) => ({ label: o.label, value: o.value }))}
      value={value}
      onChange={(v) => onChange(v as SearchMode)}
      block
      className="max-w-xs"
    />
  )
}
