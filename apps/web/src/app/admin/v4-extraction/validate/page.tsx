"use client"

import { Empty, Typography } from "antd"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"

export default function ValidateIndexPage() {
  const router = useRouter()

  // Try to find a job with pending review items
  const { data: recentJobs } = useQuery({
    queryKey: ["v4-validation-pending"],
    queryFn: async () => {
      // Use material systems endpoint to check for pending items
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"}/api/v4/material-systems?has_pending_review=true`,
        { headers: { "Content-Type": "application/json" } },
      )
      if (!response.ok) return []
      const result = await response.json()
      return result.data ?? []
    },
    refetchInterval: 30_000,
  })

  const totalPending =
    recentJobs?.reduce((sum: number, sys: { pending_review_count: number }) => sum + sys.pending_review_count, 0) ?? 0

  if (totalPending > 0 && recentJobs && recentJobs.length > 0) {
    const firstPending = recentJobs[0]
    router.push(`/admin/v4-extraction/validate/${firstPending.name}`)
    return null
  }

  return (
    <div style={{ padding: 24 }}>
      <Empty
        description={
          <Typography.Text type="secondary">
            暂无待审核项目 / No pending review items
          </Typography.Text>
        }
      />
    </div>
  )
}
