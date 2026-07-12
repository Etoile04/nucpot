"use client"

import { Spin, Empty, Typography } from "antd"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import type { RagCitation } from "@/lib/rag-api"

const { Paragraph } = Typography

interface SemanticSearchResultsProps {
  readonly answer: string
  readonly citations: ReadonlyArray<RagCitation>
  readonly loading: boolean
  readonly error: string | null
}

function CitationChunkCard({
  citation,
}: {
  readonly citation: RagCitation
}) {
  const { id, source, excerpt, confidence, url } = citation

  const card = (
    <div
      className="rounded-lg border border-gray-700 bg-gray-800/50 p-4 transition-colors hover:border-gray-600"
      data-testid={`rag-chunk-${id}`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-gray-300 font-medium truncate mr-2">{source}</span>
        <ConfidenceBadge value={confidence} size="sm" showLabel />
      </div>
      {excerpt && (
        <Paragraph
          className="text-gray-400 text-xs leading-relaxed !mb-0"
          ellipsis={{ rows: 4, expandable: "collapsible" }}
        >
          {excerpt}
        </Paragraph>
      )}
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block mt-2 text-blue-400 hover:text-blue-300 text-xs transition-colors"
        >
          查看来源
        </a>
      )}
    </div>
  )

  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="block"
        aria-label={`引用来源: ${source}`}
      >
        {card}
      </a>
    )
  }

  return card
}

export function SemanticSearchResults({
  answer,
  citations,
  loading,
  error,
}: SemanticSearchResultsProps) {
  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Spin tip="正在检索..." />
      </div>
    )
  }

  if (error) {
    return <Empty description={`检索失败：${error}`} />
  }

  if (!answer) {
    return (
      <Empty description="请输入查询内容进行语义检索" />
    )
  }

  return (
    <div className="space-y-6">
      {/* Answer */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-900/20 p-5">
        <h3 className="text-sm font-semibold text-blue-300 uppercase tracking-wider mb-3">
          AI 回答
        </h3>
        <p className="text-gray-100 text-sm leading-relaxed whitespace-pre-wrap">
          {answer}
        </p>
      </div>

      {/* Citations / Chunks */}
      {citations.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
            相关片段 ({citations.length})
          </h3>
          <div className="grid grid-cols-1 gap-3">
            {citations.map((citation) => (
              <CitationChunkCard key={citation.id} citation={citation} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
