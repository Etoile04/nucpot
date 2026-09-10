"use client"

/**
 * LiteratureGraphView — Layout B data-fetching wrapper (NFM-4553 G1-E).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.1
 *
 * Thin client wrapper that:
 *   1. Fetches /api/v1/literature/{id}
 *   2. Maps the API payload onto <LiteratureGraphLayout>'s readonly
 *      `payload` shape (extraction_results → extractionResults).
 *   3. Owns navigation: back to /literature, switch to Layout A at
 *      /admin/review/queue.
 *
 * The original flat LiteratureDetailView (extraction_results as a
 * provenance-grouped <Collapse>) is preserved at /literature/[id]?
 * view=flat for the screenshot regression baseline; Layout B is the
 * default per spec AC-3.
 */

import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { literatureApi, type LiteratureDetail } from "@/lib/api-client"
import { LiteratureGraphLayout } from "@/components/review/LiteratureGraphLayout"

interface LiteratureGraphViewProps {
  readonly literatureId: string
}

export default function LiteratureGraphView({
  literatureId,
}: LiteratureGraphViewProps) {
  const router = useRouter()
  const [detail, setDetail] = useState<LiteratureDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await literatureApi.get(literatureId)
      setDetail(data)
    } catch (err) {
      if (err instanceof Error && err.message.includes("404")) {
        setError("文献不存在")
      } else {
        const msg = err instanceof Error ? err.message : "加载文献详情失败"
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }, [literatureId])

  useEffect(() => {
    void fetchDetail()
  }, [fetchDetail])

  const handleBack = useCallback(() => {
    void router.push("/literature")
  }, [router])

  const handleSwitchToReviewView = useCallback(() => {
    // Layout A is /admin/review/queue (G1-F NFM-4554). Pass the
    // literature id as a query param so the queue can scope to it.
    void router.push(
      `/admin/review/queue?literature_id=${encodeURIComponent(literatureId)}`,
    )
  }, [router, literatureId])

  return (
    <LiteratureGraphLayout
      literatureId={literatureId}
      payload={
        detail
          ? {
              id: detail.id,
              title: detail.title ?? null,
              extractionResults: detail.extraction_results ?? [],
            }
          : null
      }
      loading={loading}
      error={error}
      onBack={handleBack}
      onSwitchToReviewView={handleSwitchToReviewView}
    />
  )
}
