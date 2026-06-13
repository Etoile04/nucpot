"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { formatDate } from "@/lib/blog/format-date"
import type { BlogPostMeta } from "@/lib/blog/types"

export default function BlogPostsAdminPage() {
  const [posts, setPosts] = useState<BlogPostMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    loadPosts()
  }, [])

  // Cancel delete confirmation when clicking elsewhere
  useEffect(() => {
    const handleClick = () => {
      if (deleteConfirm) {
        setDeleteConfirm(null)
      }
    }

    if (deleteConfirm) {
      document.addEventListener("click", handleClick)
      return () => document.removeEventListener("click", handleClick)
    }
  }, [deleteConfirm])

  const loadPosts = async () => {
    try {
      const response = await fetch("/api/admin/blog/posts")
      const result = await response.json()

      if (result.success) {
        setPosts(result.data)
      }
    } catch (error) {
      console.error("Failed to load posts:", error)
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (slug: string) => {
    if (!deleteConfirm) {
      setDeleteConfirm(slug)
      return
    }

    setDeleting(true)
    try {
      const response = await fetch(`/api/admin/blog/posts/${slug}`, {
        method: "DELETE",
      })

      const result = await response.json()

      if (result.success) {
        // Remove from local state
        setPosts(posts.filter((p) => p.slug !== slug))
        setDeleteConfirm(null)
      } else {
        alert(result.error || "删除失败")
      }
    } catch (error) {
      alert("删除失败")
    } finally {
      setDeleting(false)
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
        文章列表
      </h1>

      <div
        style={{
          marginBottom: "1.5rem",
          padding: "1rem",
          background: "#f5f5f5",
          borderRadius: 4,
          border: "1px solid #d9d9d9",
        }}
      >
        <p style={{ margin: 0, color: "#666", fontSize: "0.875rem" }}>
          共 {posts.length} 篇文章
        </p>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        {posts.map((post) => (
          <div
            key={post.slug}
            style={{
              padding: "1rem",
              border: "1px solid #d9d9d9",
              borderRadius: 4,
              background: "#fff",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
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
            <div style={{ display: "flex", gap: "0.5rem" }}>
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
                查看
              </Link>
              <Link
                href={`/admin/blog/posts/${post.slug}/edit`}
                style={{
                  padding: "0.5rem 1rem",
                  fontSize: "0.875rem",
                  border: "1px solid #d9d9d9",
                  borderRadius: 4,
                  background: "#fff",
                  textDecoration: "none",
                  color: "#52c41a",
                }}
              >
                编辑
              </Link>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  handleDelete(post.slug)
                }}
                disabled={deleting}
                style={{
                  padding: "0.5rem 1rem",
                  fontSize: "0.875rem",
                  border: deleteConfirm === post.slug
                    ? "1px solid #ff4d4f"
                    : "1px solid #d9d9d9",
                  borderRadius: 4,
                  background: deleting
                    ? "#bfbfbf"
                    : deleteConfirm === post.slug
                    ? "#fff1f0"
                    : "#fff",
                  color: deleteConfirm === post.slug ? "#ff4d4f" : "#ff4d4f",
                  cursor: deleting ? "not-allowed" : "pointer",
                }}
              >
                {deleting
                  ? "删除中..."
                  : deleteConfirm === post.slug
                  ? "确认删除"
                  : "删除"}
              </button>
            </div>
          </div>
        ))}

        {posts.length === 0 && (
          <div
            style={{
              textAlign: "center",
              padding: "3rem",
              color: "#999",
            }}
          >
            <p>暂无文章</p>
          </div>
        )}
      </div>
    </div>
  )
}
