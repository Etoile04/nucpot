import type { Metadata } from "next"
import { notFound } from "next/navigation"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { getAllPosts, getPostBySlug, getAllSlugs } from "@/lib/blog/posts"
import { formatDate } from "@/lib/blog/format-date"
import { BlogNavigation } from "@/components/blog"
import "../blog.css"

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
        padding: "3rem 1.5rem",
      }}
    >
      <article>
        <header style={{ marginBottom: "3rem" }}>
          <h1
            style={{
              fontSize: "2.5rem",
              fontWeight: 700,
              lineHeight: 1.2,
              marginBottom: "1rem",
              letterSpacing: "-0.02em",
            }}
          >
            {post.frontmatter.title}
          </h1>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "1.5rem",
              alignItems: "center",
              color: "var(--color-text-secondary)",
              fontSize: "0.9375rem",
              marginBottom: "1rem",
            }}
          >
            <time dateTime={post.frontmatter.date}>
              {formatDate(post.frontmatter.date)}
            </time>
            <span>作者：{post.frontmatter.author}</span>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
            {post.frontmatter.tags.map((tag) => (
              <span
                key={tag}
                style={{
                  display: "inline-block",
                  padding: "0.25rem 0.625rem",
                  fontSize: "0.8125rem",
                  lineHeight: 1.5,
                  borderRadius: 4,
                  background: "var(--color-surface-elevated, #f5f5f5)",
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
            paddingTop: "2rem",
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
