"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter, useParams } from "next/navigation"
import ReactMarkdown from "react-markdown"
import ImageUpload from "@/components/admin/ImageUpload"
import { blogApi } from "@/lib/api-client"
import { useFormSubmit } from "@/hooks/useFormSubmit"

export default function EditBlogPostPage() {
  const router = useRouter()
  const params = useParams()
  const slug = params.slug as string
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const [title, setTitle] = useState("")
  const [author, setAuthor] = useState("")
  const [tags, setTags] = useState("")
  const [summary, setSummary] = useState("")
  const [content, setContent] = useState("")
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [isPreviewMode, setIsPreviewMode] = useState(false)

  const submit = useFormSubmit<
    {
      slug: string
      title: string
      content: string
      summary: string
      tags: string[]
      author_name: string
    },
    unknown
  >({
    mutationFn: (values) =>
      blogApi.update(values.slug, {
        title: values.title,
        content: values.content,
        summary: values.summary,
        tags: values.tags,
        author_name: values.author_name,
      }),
    errorFallback: "更新文章失败",
    onSuccess: () => {
      setTimeout(() => {
        router.push("/admin/blog/posts")
      }, 2000)
    },
  })

  const handleImageInsert = (markdown: string) => {
    setContent((prev) => {
      const textarea = textareaRef.current
      if (!textarea) return prev

      const start = textarea.selectionStart
      const end = textarea.selectionEnd
      const newText = prev.substring(0, start) + "\n" + markdown + "\n" + prev.substring(end)

      setTimeout(() => {
        textarea.focus()
        const newPosition = start + markdown.length + 2
        textarea.setSelectionRange(newPosition, newPosition)
      }, 0)

      return newText
    })
  }

  useEffect(() => {
    loadPost()
  }, [slug])

  const loadPost = async () => {
    try {
      const post = await blogApi.get(slug)

      setTitle(post.title)
      setAuthor(post.author_name ?? "")
      setTags(Array.isArray(post.tags) ? post.tags.join(", ") : String(post.tags ?? ""))
      setSummary(post.summary ?? "")
      setContent(post.content ?? "")
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "加载文章失败")
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await submit.submit({
        slug,
        title,
        content,
        summary,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        author_name: author,
      })
    } catch {
      // surfaced via submit.error
    }
  }

  if (loading) {
    return (
      <div style={{ padding: "2rem", textAlign: "center" }}>
        <p>加载中...</p>
      </div>
    )
  }

  if (loadError) {
    return (
      <div style={{ padding: "2rem" }}>
        <div
          style={{
            padding: "1rem",
            background: "#fff2f0",
            border: "1px solid #ffccc7",
            borderRadius: 4,
            color: "#ff4d4f",
          }}
          role="alert"
        >
          {loadError}
        </div>
      </div>
    )
  }

  return (
    <div style={{ padding: "2rem" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "1.5rem",
        }}
      >
        <h1
          style={{
            fontSize: "1.75rem",
            fontWeight: 600,
            margin: 0,
          }}
        >
          编辑文章
        </h1>
        <button
          type="button"
          onClick={() => setIsPreviewMode(!isPreviewMode)}
          style={{
            padding: "0.5rem 1rem",
            fontSize: "0.875rem",
            fontWeight: 500,
            border: "1px solid #d9d9d9",
            borderRadius: 4,
            background: isPreviewMode ? "#1890ff" : "#fff",
            color: isPreviewMode ? "#fff" : "#1890ff",
            cursor: "pointer",
          }}
        >
          {isPreviewMode ? "编辑模式" : "预览模式"}
        </button>
      </div>

      {submit.status === "error" && submit.error ? (
        <div
          style={{
            marginBottom: "1.5rem",
            padding: "1rem",
            background: "#fff2f0",
            border: "1px solid #ffccc7",
            borderRadius: 4,
            color: "#ff4d4f",
          }}
          role="alert"
        >
          {submit.error}
        </div>
      ) : null}

      {submit.status === "success" ? (
        <div
          style={{
            marginBottom: "1.5rem",
            padding: "1rem",
            background: "#f6ffed",
            border: "1px solid #b7eb8f",
            borderRadius: 4,
            color: "#52c41a",
          }}
          role="status"
        >
          文章更新成功！正在跳转...
        </div>
      ) : null}

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
          <ImageUpload onImageInsert={handleImageInsert} />
          <textarea
            ref={textareaRef}
            id="content"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            required
            rows={15}
            style={{
              width: "100%",
              marginTop: "1rem",
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
            disabled={submit.isSubmitting}
            style={{
              padding: "0.625rem 1.25rem",
              fontSize: "1rem",
              fontWeight: 500,
              color: "#fff",
              background: submit.isSubmitting ? "#bfbfbf" : "#1890ff",
              border: "none",
              borderRadius: 4,
              cursor: submit.isSubmitting ? "not-allowed" : "pointer",
            }}
          >
            {submit.isSubmitting ? "保存中..." : "保存修改"}
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

      {/* Preview Mode */}
      {isPreviewMode && (
        <div
          style={{
            maxWidth: 800,
            margin: "0 auto",
            padding: "2rem",
            background: "#fff",
            border: "1px solid #d9d9d9",
            borderRadius: 8,
          }}
        >
          <div style={{ marginBottom: "2rem" }}>
            <h2
              style={{
                fontSize: "2rem",
                fontWeight: 700,
                marginBottom: "1rem",
                color: "#000",
              }}
            >
              {title || "文章标题"}
            </h2>
            <div
              style={{
                fontSize: "0.875rem",
                color: "#666",
                display: "flex",
                gap: "1rem",
                marginBottom: "1rem",
              }}
            >
              {author && <span>作者：{author}</span>}
              {tags && (
                <span>
                  标签：
                  {tags
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean)
                    .join(", ")}
                </span>
              )}
            </div>
            {summary && (
              <p
                style={{
                  fontSize: "1rem",
                  color: "#666",
                  lineHeight: 1.6,
                  padding: "1rem",
                  background: "#f5f5f5",
                  borderRadius: 4,
                }}
              >
                {summary}
              </p>
            )}
          </div>

          <div
            className="blog-prose"
            style={{
              fontSize: "1rem",
              lineHeight: 1.75,
            }}
          >
            <ReactMarkdown>{content || "*文章内容将显示在这里*"}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  )
}
