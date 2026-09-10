"use client"

import { Spin, Empty, Typography } from "antd"
import { ConfidenceBadge } from "@/components/shared/ConfidenceBadge"
import type { RagCitation, RagFallbackInfo } from "@/lib/rag-api"
import { RagFallbackBadge } from "./RagFallbackBadge"

const { Paragraph } = Typography

interface SemanticSearchResultsProps {
  readonly answer: string
  readonly citations: ReadonlyArray<RagCitation>
  readonly loading: boolean
  readonly error: string | null
  /** NFM-4539 RAG-B: transparent degradation envelope. */
  readonly fallback?: RagFallbackInfo
  /** True once the user has submitted at least one query (vs. cold mount). */
  readonly hasSearched?: boolean
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
        <span
          className="inline-block mt-2 text-blue-400 hover:text-blue-300 text-xs transition-colors"
          aria-label={`引用来源: ${source}`}
        >
          查看来源
        </span>
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

/**
 * NFM-4539 RAG-C / §3.2 / UAT-6: when the answer is empty after a real
 * query, render honest neutral copy instead of an empty card.
 * Pre-RAG-C the surface showed a generic spinner — users could not
 * tell whether the system had indexed the question or whether they
 * had simply asked about something the KG doesn't have.
 */
function EmptyCoverageCard() {
  return (
    <div
      data-testid="rag-empty-coverage"
      className="rounded-lg border border-gray-700 bg-gray-800/40 p-5 text-center space-y-2"
    >
      <p className="text-gray-200 text-sm font-medium">知识库暂未覆盖该问题</p>
      <p className="text-gray-400 text-xs">
        可尝试调整关键词或前往「势函数检索」按类型/元素筛选
      </p>
    </div>
  )
}

export function SemanticSearchResults({
  answer,
  citations,
  loading,
  error,
  fallback,
  hasSearched = false,
}: SemanticSearchResultsProps) {
  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Spin tip="正在检索..."><div /></Spin>
      </div>
    )
  }

  if (error) {
    return <Empty description={`检索失败：${error}`} />
  }

  // NFM-4539 RAG-B / AC-4 / §3.2: the transparent-degradation badge is
  // the canonical owner of the fallback signal (NFM-4545 visual QA —
  // the user must always see exactly one amber pill).  Render it
  // whenever fallback.used=true regardless of the answer state so the
  // "ILIKE ran but found nothing" case still surfaces the caveat.
  const fallbackBadge = fallback?.used ? (
    <div className="flex">
      <RagFallbackBadge fallback={fallback} />
    </div>
  ) : null

  if (!answer) {
    // NFM-4539 RAG-C / UAT-6: only show honest empty coverage once the
    // user has actually queried — before that, an empty Empty is the
    // right "waiting for input" state.
    if (hasSearched) {
      return (
        <div className="space-y-4">
          {fallbackBadge}
          <EmptyCoverageCard />
        </div>
      )
    }
    return <Empty description="请输入查询内容进行语义检索" />
  }

  return (
    <div className="space-y-6">
      {fallbackBadge}

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
