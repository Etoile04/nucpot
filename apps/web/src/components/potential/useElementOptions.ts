"use client"

import { useCallback, useEffect, useState } from "react"

interface StatsEnvelope {
  readonly success?: boolean
  readonly data?: {
    readonly elements?: readonly string[]
  }
  readonly error?: string
}

interface ElementOptionsState {
  readonly elements: readonly string[]
  readonly loading: boolean
  readonly error: string | null
  readonly retry: () => void
}

/**
 * Candidate element list for the potential-function element filter
 * (/browse and /search). Reads the ApiResponse envelope from /api/stats
 * and keeps loading / error / data states distinct so an outage renders
 * an error state with retry instead of 「无匹配元素」 (NFM-4310, BUG-29).
 */
export function useElementOptions(): ElementOptionsState {
  const [elements, setElements] = useState<readonly string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  const retry = useCallback(() => {
    setAttempt((n) => n + 1)
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    fetch("/api/stats")
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`元素数据加载失败 (HTTP ${res.status})`)
        }
        const body = (await res.json()) as StatsEnvelope
        if (!body.success || !Array.isArray(body.data?.elements)) {
          throw new Error(body.error ?? "元素数据格式错误")
        }
        return body.data.elements
      })
      .then((els) => {
        if (cancelled) return
        setElements(els)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setElements([])
        setError(err instanceof Error ? err.message : "元素数据加载失败")
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [attempt])

  return { elements, loading, error, retry }
}
