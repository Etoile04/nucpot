"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import type { BlogPostMeta } from "@/lib/blog/types"

interface DocumentationSection {
  readonly title: string
  readonly posts: readonly BlogPostMeta[]
}

interface BlogSidebarProps {
  /**
   * Post list, fetched server-side by the parent and passed down. BlogSidebar
   * is a client component (it needs `usePathname`), so it must NOT import the
   * server-only `posts.ts` (which uses Node `fs`) — doing so pulls `fs` into
   * the client bundle and breaks the build for every route that imports this
   * component transitively via the blog barrel.
   */
  readonly posts: readonly BlogPostMeta[]
}

export function BlogSidebar({ posts }: BlogSidebarProps) {
  const pathname = usePathname()

  // Group posts by documentation sections based on tags
  const sections: readonly DocumentationSection[] = [
    {
      title: "快速开始",
      posts: posts.filter((post) => post.tags.includes("getting-started")),
    },
    {
      title: "API 文档",
      posts: posts.filter((post) => post.tags.includes("api")),
    },
    {
      title: "用户指南",
      posts: posts.filter((post) => post.tags.includes("guide")),
    },
    {
      title: "技术文章",
      posts: posts.filter((post) => post.tags.includes("technical")),
    },
  ]

  const hasContent = sections.some((section) => section.posts.length > 0)

  if (!hasContent) {
    return null
  }

  return (
    <aside className="blog-sidebar" aria-label="文档导航">
      <nav className="blog-sidebar-nav">
        <Link href="/blog" className="blog-sidebar-logo">
          技术博客
        </Link>

        {sections.map(
          (section) =>
            section.posts.length > 0 && (
              <div key={section.title} className="blog-sidebar-section">
                <h3 className="blog-sidebar-section-title">{section.title}</h3>
                <ul className="blog-sidebar-list">
                  {section.posts.map((post) => (
                    <li key={post.slug} className="blog-sidebar-item">
                      <Link
                        href={`/blog/${post.slug}`}
                        className={`blog-sidebar-link ${
                          pathname === `/blog/${post.slug}`
                            ? "blog-sidebar-link-active"
                            : ""
                        }`}
                      >
                        {post.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )
        )}
      </nav>
    </aside>
  )
}
