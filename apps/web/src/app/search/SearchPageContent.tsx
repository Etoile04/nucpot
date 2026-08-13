"use client"

import { useCallback } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { Typography } from "antd"
import { SearchView } from "./SearchView"
import { RagSearchView } from "@/components/search/RagSearchView"
import {
  SearchModeToggle,
  type SearchMode,
} from "@/components/search/SearchModeToggle"

const { Title, Text } = Typography

function isValidMode(value: string | null): SearchMode {
  if (value === "text" || value === "semantic") {
    return value
  }
  return "text"
}

export function SearchPageContent() {
  const searchParams = useSearchParams()
  const router = useRouter()

  const mode: SearchMode = isValidMode(searchParams.get("mode"))

  const handleModeChange = useCallback(
    (newMode: SearchMode) => {
      const params = new URLSearchParams(searchParams.toString())
      params.set("mode", newMode)
      router.push(`/search?${params.toString()}`)
    },
    [searchParams, router],
  )

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      {/* Header with mode toggle */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <Title level={2} className="!m-0 text-white">
            {mode === "text" ? "高级检索" : "语义检索"}
          </Title>
          <Text type="secondary">
            {mode === "text"
              ? "按类型、元素或关键字检索势函数库"
              : "使用 AI 语义检索知识图谱中的核材料数据"}
          </Text>
        </div>
        <SearchModeToggle value={mode} onChange={handleModeChange} />
      </div>

      {/* Mode-specific content */}
      {mode === "text" ? <SearchView /> : <RagSearchView />}
    </main>
  )
}
