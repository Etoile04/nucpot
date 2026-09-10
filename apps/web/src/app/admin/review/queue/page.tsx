/**
 * /admin/review/queue — Layout A (proof-reading queue).
 *
 * NFM-4554 G1-F — AC-3 (default route is Layout A) + AC-4
 * (admin 兼任过渡期 + future domain_expert role).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.2.
 */
"use client"

import { useEffect } from "react"
import { Spin } from "antd"
import { useRouter } from "next/navigation"
import { useAuth } from "@/components/AuthProvider"
import { ReviewQueueContent } from "@/components/admin/review-queue/ReviewQueueContent"

/**
 * Resolve whether the current user may access the proof-reading queue.
 *
 * Transition period (NFM-4554 AC-4 + spec §2 BUG-08 carryover):
 * admin 兼任 → admins have access until the domain_expert role ships.
 * Long-term: domain_expert becomes the canonical reviewer role.
 */
export function canAccessReviewQueue(blogRole: string | null | undefined): boolean {
  if (!blogRole) return false
  const role = blogRole.toLowerCase()
  return role === "admin" || role === "domain_expert"
}

export default function ReviewQueuePage() {
  const router = useRouter()
  const { user, loading } = useAuth()

  useEffect(() => {
    if (loading) return
    if (!user) {
      router.replace("/login")
      return
    }
    if (!canAccessReviewQueue(user.blog_role)) {
      router.replace("/")
    }
  }, [loading, user, router])

  if (loading || !user || !canAccessReviewQueue(user.blog_role)) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "60vh",
        }}
      >
        <Spin size="large" />
      </div>
    )
  }

  return <ReviewQueueContent />
}
