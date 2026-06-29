"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { formatDate } from "@/lib/blog/format-date"

interface ReviewPost {
  id: string
  slug: string
  title: string
  date: string
  author: string
  summary: string
  tags: string[]
  status: string
}

export default function ReviewQueuePage() {
  const [posts, setPosts] = useState<ReviewPost[]>([])
  const [loading, setLoading] = useState(true)
  const [actionInProgress, setActionInProgress] = useState<string | null>(null)
  const [showRejectDialog, setShowRejectDialog] = useState<string | null>(null)
  const [rejectionReason, setRejectionReason] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [filteredPosts, setFilteredPosts] = useState<ReviewPost[]>([])

  useEffect(() => {
    loadPendingReviews()
  }, [])

  useEffect(() => {
    if (!searchQuery.trim()) {
      setFilteredPosts(posts)
    } else {
      const query = searchQuery.toLowerCase()
      const filtered = posts.filter(
        (post) =>
          post.title.toLowerCase().includes(query) ||
          post.author.toLowerCase().includes(query) ||
          post.tags.some((tag) => tag.toLowerCase().includes(query))
      )
      setFilteredPosts(filtered)
    }
  }, [searchQuery, posts])

  const loadPendingReviews = async () => {
    try {
      // TODO: Update API endpoint to filter by status
      const response = await fetch("/api/admin/blog/posts")
      const result = await response.json()

      if (result.success) {
        // Filter for under_review status
        const pendingPosts = result.data.filter(
          (post: ReviewPost) => post.status === "under_review"
        )
        setPosts(pendingPosts)
      }
    } catch (error) {
      console.error("Failed to load pending reviews:", error)
    } finally {
      setLoading(false)
    }
  }

  const handleApprove = async (slug: string) => {
    setActionInProgress(slug)
    try {
      const response = await fetch(`/api/admin/blog/posts/${slug}/workflow`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "approve" }),
      })

      const result = await response.json()

      if (result.success) {
        // Remove from list
        setPosts(posts.filter((p) => p.slug !== slug))
      } else {
        alert(result.error || "审批失败")
      }
    } catch (error) {
      alert("审批失败")
    } finally {
      setActionInProgress(null)
    }
  }

  const handleReject = async (slug: string) => {
    if (!showRejectDialog) {
      setShowRejectDialog(slug)
      return
    }

    if (!rejectionReason.trim()) {
      alert("请提供拒绝原因")
      return
    }

    setActionInProgress(slug)
    try {
      const response = await fetch(`/api/admin/blog/posts/${slug}/workflow`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "reject",
          rejection_reason: rejectionReason,
        }),
      })

      const result = await response.json()

      if (result.success) {
        // Remove from list
        setPosts(posts.filter((p) => p.slug !== slug))
        setShowRejectDialog(null)
        setRejectionReason("")
      } else {
        alert(result.error || "拒绝失败")
      }
    } catch (error) {
      alert("拒绝失败")
    } finally {
      setActionInProgress(null)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: "2rem", textAlign: "center" }}>
        <p>加载中...</p>
      </div>
    )
  }

  return (
    <div style={{ padding: "2rem" }}>
      <h1
        style={{
          fontSize: "1.75rem",
          fontWeight: 600,
          marginBottom: "1.5rem",
        }}
      >
        待审核文章
      </h1>

      {/* Search Bar */}
      <div
        style={{
          marginBottom: "1.5rem",
          padding: "1rem",
          background: "#fff",
          borderRadius: 4,
          border: "1px solid #d9d9d9",
        }}
      >
        <input
          type="text"
          placeholder="搜索文章标题、作者或标签..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            width: "100%",
            padding: "0.5rem",
            border: "1px solid #d9d9d9",
            borderRadius: 4,
            fontSize: "1rem",
          }}
        />
        <p style={{ margin: "0.5rem 0 0 0", color: "#666", fontSize: "0.875rem" }}>
          {searchQuery.trim()
            ? `找到 ${filteredPosts.length} 篇待审核文章`
            : `共 ${posts.length} 篇待审核文章`}
        </p>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        {filteredPosts.map((post) => (
          <div
            key={post.slug}
            style={{
              padding: "1rem",
              border: "1px solid #d9d9d9",
              borderRadius: 4,
              background: "#fff",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                marginBottom: "1rem",
              }}
            >
              <div style={{ flex: 1 }}>
                <h3
                  style={{
                    fontSize: "1rem",
                    fontWeight: 500,
                    margin: "0 0 0.5rem",
                  }}
                >
                  {post.title}
                </h3>
                <div
                  style={{
                    fontSize: "0.875rem",
                    color: "#666",
                    display: "flex",
                    gap: "1rem",
                  }}
                >
                  <span>{formatDate(post.date)}</span>
                  <span>作者：{post.author}</span>
                  <span>标签：{post.tags.join(", ")}</span>
                </div>
              </div>
              <Link
                href={`/blog/${post.slug}`}
                target="_blank"
                style={{
                  padding: "0.5rem 1rem",
                  fontSize: "0.875rem",
                  border: "1px solid #d9d9d9",
                  borderRadius: 4,
                  background: "#fff",
                  textDecoration: "none",
                  color: "#1890ff",
                }}
              >
                查看全文
              </Link>
            </div>

            <p
              style={{
                fontSize: "0.875rem",
                color: "#666",
                margin: "0 0 1rem 0",
                lineHeight: "1.5",
              }}
            >
              {post.summary}
            </p>

            {showRejectDialog === post.slug ? (
              <div
                style={{
                  padding: "1rem",
                  background: "#fff1f0",
                  border: "1px solid #ffccc7",
                  borderRadius: 4,
                }}
              >
                <textarea
                  value={rejectionReason}
                  onChange={(e) => setRejectionReason(e.target.value)}
                  placeholder="请提供拒绝原因..."
                  style={{
                    width: "100%",
                    padding: "0.5rem",
                    border: "1px solid #d9d9d9",
                    borderRadius: 4,
                    fontSize: "0.875rem",
                    minHeight: "80px",
                    marginBottom: "0.5rem",
                  }}
                />
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <button
                    onClick={() => handleReject(post.slug)}
                    disabled={actionInProgress === post.slug}
                    style={{
                      padding: "0.5rem 1rem",
                      fontSize: "0.875rem",
                      border: "1px solid #ff4d4f",
                      borderRadius: 4,
                      background: actionInProgress === post.slug
                        ? "#bfbfbf"
                        : "#ff4d4f",
                      color: "#fff",
                      cursor: actionInProgress === post.slug
                        ? "not-allowed"
                        : "pointer",
                    }}
                  >
                    {actionInProgress === post.slug
                      ? "提交中..."
                      : "确认拒绝"}
                  </button>
                  <button
                    onClick={() => {
                      setShowRejectDialog(null)
                      setRejectionReason("")
                    }}
                    disabled={actionInProgress === post.slug}
                    style={{
                      padding: "0.5rem 1rem",
                      fontSize: "0.875rem",
                      border: "1px solid #d9d9d9",
                      borderRadius: 4,
                      background: "#fff",
                      cursor: actionInProgress === post.slug
                        ? "not-allowed"
                        : "pointer",
                    }}
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button
                  onClick={() => handleApprove(post.slug)}
                  disabled={actionInProgress === post.slug}
                  style={{
                    padding: "0.5rem 1rem",
                    fontSize: "0.875rem",
                    border: "1px solid #52c41a",
                    borderRadius: 4,
                    background:
                      actionInProgress === post.slug ? "#bfbfbf" : "#52c41a",
                    color: "#fff",
                    cursor:
                      actionInProgress === post.slug
                        ? "not-allowed"
                        : "pointer",
                  }}
                >
                  {actionInProgress === post.slug
                    ? "处理中..."
                    : "通过"}
                </button>
                <button
                  onClick={() => setShowRejectDialog(post.slug)}
                  disabled={actionInProgress === post.slug}
                  style={{
                    padding: "0.5rem 1rem",
                    fontSize: "0.875rem",
                    border: "1px solid #ff4d4f",
                    borderRadius: 4,
                    background:
                      actionInProgress === post.slug ? "#bfbfbf" : "#fff",
                    color: "#ff4d4f",
                    cursor:
                      actionInProgress === post.slug
                        ? "not-allowed"
                        : "pointer",
                  }}
                >
                  {actionInProgress === post.slug ? "处理中..." : "拒绝"}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      {filteredPosts.length === 0 && (
        <div
          style={{
            textAlign: "center",
            padding: "3rem",
            color: "#999",
          }}
        >
          <p>
            {searchQuery.trim()
              ? "没有找到匹配的文章"
              : "暂无待审核文章"}
          </p>
        </div>
      )}
    </div>
  )
}
