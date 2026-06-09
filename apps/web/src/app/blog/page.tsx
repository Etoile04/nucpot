import type { Metadata } from "next"
import { getAllPosts } from "@/lib/blog/posts"
import type { BlogPostMeta } from "@/lib/blog/types"
import { BlogCard } from "@/components/blog"

export const metadata: Metadata = {
  title: "技术博客 — 核燃料与材料物性数据库",
  description: "核材料科学、数据库技术、核工程领域的技术文章",
}

function BlogListClientWrapper({
  posts,
}: {
  readonly posts: readonly BlogPostMeta[]
}) {
  return (
    <div>
      <BlogListContent posts={posts} />
    </div>
  )
}

function BlogListContent({
  posts,
}: {
  readonly posts: readonly BlogPostMeta[]
}) {
  return (
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
  )
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
        padding: "2rem 1.5rem",
      }}
    >
      <header style={{ marginBottom: "2rem" }}>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 700, margin: "0 0 0.5rem" }}>
          技术博客
        </h1>
        <p style={{ color: "var(--color-text-secondary)", margin: 0 }}>
          核材料科学、数据库技术与核工程领域的技术文章
        </p>
      </header>

      <nav
        aria-label="文章标签筛选"
        style={{
          marginBottom: "2rem",
          padding: "1rem",
          background: "var(--color-surface-elevated)",
          borderRadius: 8,
          border: "1px solid var(--color-border)",
        }}
      >
        <div style={{ fontSize: "0.8125rem", marginBottom: "0.5rem", color: "var(--color-text-secondary)" }}>
          标签筛选
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
              style={{
                display: "inline-block",
                padding: "0.25rem 0.625rem",
                fontSize: "0.8125rem",
                borderRadius: 4,
                border: "1px solid var(--color-border)",
                color: "var(--color-text-secondary)",
                cursor: "default",
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
        <BlogListClientWrapper posts={posts} />
      )}
    </main>
  )
}
