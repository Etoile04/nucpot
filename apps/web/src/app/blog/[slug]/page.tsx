import type { Metadata } from "next"
import { notFound } from "next/navigation"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { ComponentPropsWithoutRef, ReactElement, ReactNode } from "react"
import { getAllPosts, getPostBySlug, getAllSlugs } from "@/lib/blog/posts"
import { formatDate } from "@/lib/blog/format-date"
import { slugifyHeadingText } from "@/lib/blog/headings"
import {
  BlogNavigation,
  BlogBreadcrumb,
  BlogTableOfContents,
  CodeBlock,
  BlogSidebar,
} from "@/components/blog"
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

interface HeadingProps extends ComponentPropsWithoutRef<"h1"> {
  readonly children?: React.ReactNode
}

/**
 * Build a ReactMarkdown heading component (h1..h6) that applies
 * `id={slugifyHeadingText(textContent)}` so TOC anchor clicks can
 * resolve via `getElementById`. Reading textContent (rather than the
 * `node` tree) keeps the slug algorithm stable across inline markup.
 */
function makeHeadingComponent(tag: "h1" | "h2" | "h3" | "h4" | "h5" | "h6") {
  return function Heading({
    children,
    ...props
  }: HeadingProps): ReactElement {
    const text = extractText(children)
    const id = slugifyHeadingText(text)
    const Tag = tag
    return (
      <Tag id={id} {...props}>
        {children}
      </Tag>
    )
  }
}

function extractText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return ""
  if (typeof node === "string" || typeof node === "number") return String(node)
  if (Array.isArray(node)) return node.map(extractText).join("")
  if (typeof node === "object" && "props" in node) {
    const child = (node as { props: { children?: ReactNode } }).props.children
    return extractText(child)
  }
  return ""
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

  const posts = getAllPosts()
  const { prev, next } = findAdjacentPosts(slug)

  return (
    <>
      <BlogSidebar posts={posts} />
      <main className="blog-container">
        <BlogBreadcrumb />
        <div className="blog-detail-layout">
        <article className="blog-article">
          <header className="blog-article-header">
            <h1 className="blog-article-title">{post.frontmatter.title}</h1>

            <div className="blog-article-meta">
              <time dateTime={post.frontmatter.date}>
                {formatDate(post.frontmatter.date)}
              </time>
              <span>作者：{post.frontmatter.author}</span>
            </div>

            <div className="blog-article-tags">
              {post.frontmatter.tags.map((tag) => (
                <span key={tag} className="blog-card-tag">
                  {tag}
                </span>
              ))}
            </div>
          </header>

          <div className="blog-prose blog-article-content">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h1: makeHeadingComponent("h1"),
                h2: makeHeadingComponent("h2"),
                h3: makeHeadingComponent("h3"),
                h4: makeHeadingComponent("h4"),
                h5: makeHeadingComponent("h5"),
                h6: makeHeadingComponent("h6"),
                pre({ children, ...props }) {
                  // Check if this is a code block
                  const childArray = children as Array<unknown>
                  if (
                    childArray.length === 1 &&
                    typeof childArray[0] === "object" &&
                    childArray[0] !== null &&
                    "type" in childArray[0] &&
                    childArray[0].type === "code"
                  ) {
                    const codeElement = childArray[0] as unknown as {
                      props?: { className?: string; children?: string }
                    }
                    const language =
                      codeElement.props?.className?.replace("language-", "") ||
                      "text"
                    return (
                      <CodeBlock language={language}>
                        {codeElement.props?.children ?? ""}
                      </CodeBlock>
                    )
                  }
                  return <pre {...props}>{children}</pre>
                },
                code({ className, children, ...props }) {
                  // Inline code
                  if (!className) {
                    return (
                      <code className="inline-code" {...props}>
                        {children}
                      </code>
                    )
                  }
                  // Code block (handled by pre component)
                  return <code className={className} {...props}>{children}</code>
                },
              }}
            >
              {post.content}
            </ReactMarkdown>
          </div>
        </article>

        <BlogTableOfContents content={post.content} />
      </div>

      <BlogNavigation prev={prev} next={next} />
      </main>
    </>
  )
}
