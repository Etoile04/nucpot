import type { Metadata } from "next"
import { getAllPosts } from "@/lib/blog/posts"
import type { BlogPostMeta } from "@/lib/blog/types"
import { BlogCard } from "@/components/blog"
import "./blog.css"

export const metadata: Metadata = {
  title: "技术博客 — 核燃料与材料物性数据库",
  description: "核材料科学、数据库技术、核工程领域的技术文章",
}

function extractUniqueTags(
  posts: readonly BlogPostMeta[]
): readonly string[] {
  const tagSet = new Set<string>()
  for (const post of posts) {
    for (const tag of post.tags) {
      tagSet.add(tag)
    }
  }
  return [...tagSet].sort()
}

export default function BlogListPage() {
  const posts = getAllPosts()
  const allTags = extractUniqueTags(posts)

  return (
    <main
      style={{
        maxWidth: "var(--max-width)",
        margin: "0 auto",
        padding: "3rem 1.5rem",
      }}
    >
      <header style={{ marginBottom: "3rem" }}>
        <h1
          style={{
            fontSize: "2.25rem",
            fontWeight: 700,
            lineHeight: 1.2,
            marginBottom: "0.75rem",
            letterSpacing: "-0.02em",
          }}
        >
          技术博客
        </h1>
        <p
          style={{
            fontSize: "1.125rem",
            lineHeight: 1.6,
            color: "var(--color-text-secondary)",
            margin: 0,
          }}
        >
          核材料科学、数据库技术与核工程领域的技术文章
        </p>
      </header>

      <nav
        aria-label="文章标签筛选"
        style={{
          marginBottom: "3rem",
          padding: "1.25rem",
          background: "var(--color-surface-elevated)",
          borderRadius: 8,
          border: "1px solid var(--color-border)",
        }}
      >
        <div
          style={{
            fontSize: "0.875rem",
            fontWeight: 500,
            marginBottom: "0.75rem",
            color: "var(--color-text)",
          }}
        >
          按标签筛选
        </div>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "0.5rem",
          }}
        >
          {allTags.map((tag) => (
            <span
              key={tag}
              className="blog-tag"
              style={{
                display: "inline-block",
                padding: "0.375rem 0.75rem",
                fontSize: "0.875rem",
                lineHeight: 1.4,
                borderRadius: 4,
                background: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                color: "var(--color-text-secondary)",
                cursor: "pointer",
                transition: "all 150ms ease",
              }}
            >
              {tag}
            </span>
          ))}
        </div>
      </nav>

      {posts.length === 0 ? (
        <div
          style={{
            textAlign: "center",
            padding: "4rem 1.5rem",
            color: "var(--color-text-secondary)",
          }}
        >
          <p>暂无文章</p>
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "1.5rem",
          }}
        >
          {posts.map((post) => (
            <BlogCard key={post.slug} post={post} />
          ))}
        </div>
      )}
    </main>
  )
}
