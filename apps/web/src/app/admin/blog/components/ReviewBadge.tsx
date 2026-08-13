"use client"

import { useEffect, useState } from "react"

interface ReviewCountState {
  readonly count: number
  readonly loading: boolean
}

/** Hook that polls the review-count endpoint and returns the count. */
function useReviewCount(pollIntervalMs: number = 30_000): ReviewCountState {
  const [count, setCount] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function fetchCount(): Promise<void> {
      try {
        const res = await fetch("/api/admin/blog/posts/review-count")
        if (!res.ok) return
        const json = await res.json()
        if (!cancelled && json.success) {
          setCount(json.data.count)
        }
      } catch {
        // Silently fail — badge will show 0
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchCount()
    const interval = setInterval(fetchCount, pollIntervalMs)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [pollIntervalMs])

  return { count, loading }
}

/** Re-export the fetcher so the review page can trigger a manual refresh. */
export async function fetchReviewCount(): Promise<number> {
  try {
    const res = await fetch("/api/admin/blog/posts/review-count")
    if (!res.ok) return 0
    const json = await res.json()
    return json.success ? json.data.count : 0
  } catch {
    return 0
  }
}

export function ReviewBadge(): React.JSX.Element {
  const { count, loading } = useReviewCount()

  if (loading || count === 0) {
    return <span />
  }

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: 18,
        height: 18,
        padding: "0 5px",
        marginLeft: 6,
        fontSize: 11,
        fontWeight: 600,
        lineHeight: 1,
        color: "#fff",
        background: "#ff4d4f",
        borderRadius: 9,
      }}
    >
      {count}
    </span>
  )
}
