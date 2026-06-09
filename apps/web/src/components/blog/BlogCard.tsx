"use client"

import Link from "next/link"
import { Card, Tag, Typography, Space } from "antd"
import { CalendarOutlined, UserOutlined } from "@ant-design/icons"
import type { BlogPostMeta } from "@/lib/blog/types"
import { formatDate } from "@/lib/blog/format-date"

interface BlogCardProps {
  readonly post: BlogPostMeta
}

export function BlogCard({ post }: BlogCardProps) {
  const { slug, title, date, summary, tags, author } = post

  return (
    <Card
      hoverable
      style={{
        marginBottom: "1.5rem",
        border: "1px solid var(--color-border)",
        borderRadius: 8,
        background: "var(--color-surface)",
      }}
    >
      <Link href={`/blog/${slug}`} style={{ textDecoration: "none", color: "inherit" }}>
        <Typography.Title
          level={3}
          style={{
            margin: "0 0 0.75rem",
            color: "var(--color-text)",
          }}
        >
          {title}
        </Typography.Title>
      </Link>

      <Space
        size="middle"
        style={{
          marginBottom: "0.75rem",
          color: "var(--color-text-secondary)",
          fontSize: "0.875rem",
        }}
      >
        <span>
          <CalendarOutlined style={{ marginRight: 4 }} />
          {formatDate(date)}
        </span>
        <span>
          <UserOutlined style={{ marginRight: 4 }} />
          {author}
        </span>
      </Space>

      <Typography.Paragraph
        style={{
          color: "var(--color-text-secondary)",
          marginBottom: "0.75rem",
        }}
        ellipsis={{ rows: 3 }}
      >
        {summary}
      </Typography.Paragraph>

      <Space size={[4, 8]} wrap>
        {tags.map((tag) => (
          <Tag key={tag} color="blue">
            {tag}
          </Tag>
        ))}
      </Space>
    </Card>
  )
}
