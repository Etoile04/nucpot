import { getAllPosts } from "@/lib/blog/posts"
import { formatDate } from "@/lib/blog/format-date"
import Link from "next/link"

export default function BlogPostsAdminPage() {
  const posts = getAllPosts()

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
