"use client"

import { useEffect } from "react"
import { Alert, Typography } from "antd"
import Link from "next/link"

const { Text } = Typography

interface ErrorProps {
  readonly error: Error & { digest?: string }
  readonly reset: () => void
}

/**
 * Route-level error boundary for /materials/[id]/graph.
 * Captures render-time errors that bypass MaterialSubgraphView's
 * in-component fetch error handling.
 */
export default function GraphError({ error, reset }: ErrorProps) {
  useEffect(() => {
    // Surface to whatever observability stack is configured; avoid console.log
    // in production.
    if (typeof window !== "undefined" && typeof window.console !== "undefined") {
      // eslint-disable-next-line no-console
      window.console.error("[MaterialSubgraph] route error", error)
    }
  }, [error])

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      <Alert
        type="error"
        showIcon
        message="无法渲染知识图谱"
        description={error.message ?? "未知错误"}
        action={
          <button
            type="button"
            onClick={reset}
            className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white text-sm"
          >
            Retry
          </button>
        }
      />
      <Text type="secondary" className="block mt-4">
        <Link href="/browse" className="text-blue-400 hover:text-blue-300">
          ← 返回材料浏览
        </Link>
      </Text>
    </main>
  )
}