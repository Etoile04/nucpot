"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"

export default function NewBlogPostPage() {
  const router = useRouter()
  const [title, setTitle] = useState("")
  const [author, setAuthor] = useState("")
  const [tags, setTags] = useState("")
  const [summary, setSummary] = useState("")
  const [content, setContent] = useState("")

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    // Create frontmatter
    const frontmatter = `---
title: ${title}
date: ${new Date().toISOString().split("T")[0]}
author: ${author}
tags: [${tags.split(",").map((t) => t.trim()).join(", ")}]
summary: ${summary}
---

${content}
`

    // In a real implementation, this would upload to the server
    // For now, just show what would be created
    alert("文章内容已生成！\n\n在实际实现中，这将上传到服务器。\n\n" + frontmatter.substring(0, 200) + "...")
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
        新建文章
      </h1>

      <form onSubmit={handleSubmit} style={{ maxWidth: 800 }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <label
            htmlFor="title"
            style={{
              display: "block",
              marginBottom: "0.5rem",
              fontWeight: 500,
            }}
          >
            标题 *
          </label>
          <input
            id="title"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid #d9d9d9",
              borderRadius: 4,
              fontSize: "1rem",
            }}
          />
        </div>

        <div style={{ marginBottom: "1.5rem" }}>
          <label
            htmlFor="author"
            style={{
              display: "block",
              marginBottom: "0.5rem",
              fontWeight: 500,
            }}
          >
            作者 *
          </label>
          <input
            id="author"
            type="text"
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            required
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid #d9d9d9",
              borderRadius: 4,
              fontSize: "1rem",
            }}
          />
        </div>

        <div style={{ marginBottom: "1.5rem" }}>
          <label
            htmlFor="tags"
            style={{
              display: "block",
              marginBottom: "0.5rem",
              fontWeight: 500,
            }}
          >
            标签（用逗号分隔）
          </label>
          <input
            id="tags"
            type="text"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="例如: 使用指南, API, 教程"
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid #d9d9d9",
              borderRadius: 4,
              fontSize: "1rem",
            }}
          />
        </div>

        <div style={{ marginBottom: "1.5rem" }}>
          <label
            htmlFor="summary"
            style={{
              display: "block",
              marginBottom: "0.5rem",
              fontWeight: 500,
            }}
          >
            摘要 *
          </label>
          <textarea
            id="summary"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            required
            rows={3}
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid #d9d9d9",
              borderRadius: 4,
              fontSize: "1rem",
              resize: "vertical",
            }}
          />
        </div>

        <div style={{ marginBottom: "1.5rem" }}>
          <label
            htmlFor="content"
            style={{
              display: "block",
              marginBottom: "0.5rem",
              fontWeight: 500,
            }}
          >
            内容（Markdown）*
          </label>
          <textarea
            id="content"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            required
            rows={15}
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid #d9d9d9",
              borderRadius: 4,
              fontSize: "1rem",
              fontFamily: "ui-monospace, monospace",
              resize: "vertical",
            }}
            placeholder="在此输入文章内容（支持 Markdown 格式）"
          />
        </div>

        <div style={{ display: "flex", gap: "1rem" }}>
          <button
            type="submit"
            style={{
              padding: "0.625rem 1.25rem",
              fontSize: "1rem",
              fontWeight: 500,
              color: "#fff",
              background: "#1890ff",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            保存文章
          </button>
          <button
            type="button"
            onClick={() => router.back()}
            style={{
              padding: "0.625rem 1.25rem",
              fontSize: "1rem",
              fontWeight: 500,
              color: "#666",
              background: "#fff",
              border: "1px solid #d9d9d9",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            取消
          </button>
        </div>
      </form>
    </div>
  )
}
