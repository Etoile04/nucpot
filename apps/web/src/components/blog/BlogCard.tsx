"use client"

import Link from "next/link"
import type { BlogPostMeta } from "@/lib/blog/types"
import { formatDate } from "@/lib/blog/format-date"

interface BlogCardProps {
  readonly post: BlogPostMeta
}

export function BlogCard({ post }: BlogCardProps) {
  const { slug, title, date, summary, tags, author } = post

  return (
    <article className="blog-card">
      <Link href={`/blog/${slug}`} className="blog-card-link">
        <h2 className="blog-card-title">{title}</h2>
      </Link>

      <div className="blog-card-meta">
        <time dateTime={date}>{formatDate(date)}</time>
        <span>{author}</span>
      </div>

      <p className="blog-card-summary">{summary}</p>

      <div className="blog-card-tags">
        {tags.map((tag) => (
          <span key={tag} className="blog-card-tag">
            {tag}
          </span>
        ))}
      </div>
    </article>
  )
}
