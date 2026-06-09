"use client"

import Link from "next/link"
import { Button } from "antd"
import { LeftOutlined, RightOutlined } from "@ant-design/icons"

interface BlogNavigationProps {
  readonly prev: { readonly slug: string; readonly title: string } | null
  readonly next: { readonly slug: string; readonly title: string } | null
}

export function BlogNavigation({ prev, next }: BlogNavigationProps) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginTop: "2rem",
        padding: "1rem 0",
        borderTop: "1px solid var(--color-border)",
      }}
    >
      {prev ? (
        <Link href={`/blog/${prev.slug}`} style={{ textDecoration: "none" }}>
          <Button icon={<LeftOutlined />} type="text">
            ← 上一篇
          </Button>
          <div
            style={{
              fontSize: "0.75rem",
              color: "var(--color-text-secondary)",
              marginTop: 4,
              maxWidth: 240,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {prev.title}
          </div>
        </Link>
      ) : (
        <div />
      )}

      {next ? (
        <Link href={`/blog/${next.slug}`} style={{ textDecoration: "none", textAlign: "right" }}>
          <Button icon={<RightOutlined />} type="text">
            下一篇 →
          </Button>
          <div
            style={{
              fontSize: "0.75rem",
              color: "var(--color-text-secondary)",
              marginTop: 4,
              maxWidth: 240,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              marginLeft: "auto",
            }}
          >
            {next.title}
          </div>
        </Link>
      ) : (
        <div />
      )}
    </div>
  )
}
