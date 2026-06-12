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
    <article
      className="blog-card"
      style={{
        padding: "1.5rem",
        marginBottom: "1.5rem",
        border: "1px solid var(--color-border)",
        borderRadius: 8,
        background: "var(--color-surface)",
        transition: "border-color 150ms ease, box-shadow 150ms ease",
      }}
    >
      <Link href={`/blog/${slug}`} style={{ textDecoration: "none", color: "inherit" }}>
        <h2
          style={{
            fontSize: "1.5rem",
            fontWeight: 600,
            lineHeight: 1.4,
            margin: "0 0 0.75rem",
            color: "var(--color-text)",
            letterSpacing: "-0.01em",
          }}
        >
          {title}
        </h2>
      </Link>

      <div
        style={{
          display: "flex",
          gap: "1.5rem",
          marginBottom: "0.75rem",
          fontSize: "0.875rem",
          color: "var(--color-text-secondary)",
        }}
      >
        <time dateTime={date}>{formatDate(date)}</time>
        <span>{author}</span>
      </div>

      <p
        style={{
          fontSize: "1rem",
          lineHeight: 1.6,
          color: "var(--color-text-secondary)",
          marginBottom: "1rem",
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {summary}
      </p>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.5rem",
        }}
      >
        {tags.map((tag) => (
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
    </article>
  )
}
