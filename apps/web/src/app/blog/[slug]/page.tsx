import type { Metadata } from "next"
import { notFound } from "next/navigation"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { getAllPosts, getPostBySlug, getAllSlugs } from "@/lib/blog/posts"
import { BlogNavigation } from "@/components/blog"

interface BlogDetailPageProps {
  readonly params: Promise<{ slug: string }>
}

export async function generateStaticParams() {
  const slugs = getAllSlugs()
  return slugs.map((slug) => ({ slug }))
}

export async function generateMetadata({
  params,
}: BlogDetailPageProps): Promise<Metadata> {
  const { slug } = await params
  const post = getPostBySlug(slug)

  if (!post) {
    return { title: "文章未找到" }
  }

  return {
    title: `${post.frontmatter.title} — 核燃料与材料物性数据库`,
    description: post.frontmatter.summary,
  }
}

function formatDate(dateString: string): string {
  const date = new Date(dateString)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}年${month}月${day}日`
}

function findAdjacentPosts(
  currentSlug: string
): {
  prev: { slug: string; title: string } | null
  next: { slug: string; title: string } | null
} {
  const posts = getAllPosts()
  const currentIndex = posts.findIndex((p) => p.slug === currentSlug)

  if (currentIndex === -1) {
    return { prev: null, next: null }
  }

  return {
    prev: currentIndex > 0 ? (posts[currentIndex - 1] ?? null) : null,
    next:
      currentIndex < posts.length - 1
        ? (posts[currentIndex + 1] ?? null)
        : null,
  }
}

export default async function BlogDetailPage({
  params,
}: BlogDetailPageProps) {
  const { slug } = await params
  const post = getPostBySlug(slug)

  if (!post) {
    notFound()
  }

  const { prev, next } = findAdjacentPosts(slug)

  return (
    <main
      style={{
        maxWidth: "var(--max-width)",
        margin: "0 auto",
        padding: "2rem 1.5rem",
      }}
    >
      <article>
        <header style={{ marginBottom: "2rem" }}>
          <h1
            style={{
              fontSize: "1.75rem",
              fontWeight: 700,
              margin: "0 0 0.75rem",
              lineHeight: 1.3,
            }}
          >
            {post.frontmatter.title}
          </h1>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "1rem",
              alignItems: "center",
              color: "var(--color-text-secondary)",
              fontSize: "0.875rem",
              marginBottom: "1rem",
            }}
          >
            <span>{formatDate(post.frontmatter.date)}</span>
            <span>作者：{post.frontmatter.author}</span>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.375rem" }}>
            {post.frontmatter.tags.map((tag) => (
              <span
                key={tag}
                style={{
                  display: "inline-block",
                  padding: "0.125rem 0.5rem",
                  fontSize: "0.75rem",
                  borderRadius: 4,
                  border: "1px solid var(--color-border)",
                  color: "var(--color-text-secondary)",
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        </header>

        <div
          className="blog-prose"
          style={{
            borderTop: "1px solid var(--color-border)",
            paddingTop: "1.5rem",
          }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {post.content}
          </ReactMarkdown>
        </div>
      </article>

      <BlogNavigation prev={prev} next={next} />
    </main>
  )
}
