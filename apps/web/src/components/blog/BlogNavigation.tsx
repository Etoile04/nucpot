"use client"

import Link from "next/link"

interface BlogNavigationProps {
  readonly prev: { readonly slug: string; readonly title: string } | null
  readonly next: { readonly slug: string; readonly title: string } | null
}

function NavItem({
  href,
  title,
  label,
  direction,
}: {
  readonly href: string
  readonly title: string
  readonly label: string
  readonly direction: "prev" | "next"
}) {
  const alignment = direction === "prev" ? "left" : "right"

  return (
    <Link
      href={href}
      style={{
        textDecoration: "none",
        color: "inherit",
        flex: 1,
        textAlign: alignment,
        display: "flex",
        flexDirection: "column",
        gap: "0.25rem",
        padding: "0.75rem 1rem",
        borderRadius: 4,
        border: "1px solid var(--color-border)",
        transition: "border-color 150ms ease, background-color 150ms ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--color-primary, #1890ff)"
        e.currentTarget.style.backgroundColor = "var(--color-surface-elevated, #f5f5f5)"
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--color-border)"
        e.currentTarget.style.backgroundColor = "transparent"
      }}
    >
      <span
        style={{
          fontSize: "0.875rem",
          fontWeight: 500,
          color: "var(--color-text)",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontSize: "0.8125rem",
          color: "var(--color-text-secondary)",
          maxWidth: 280,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {title}
      </span>
    </Link>
  )
}

export function BlogNavigation({ prev, next }: BlogNavigationProps) {
  return (
    <nav
      aria-label="文章导航"
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "stretch",
        gap: "1rem",
        marginTop: "3rem",
        paddingTop: "2rem",
        borderTop: "1px solid var(--color-border)",
      }}
    >
      {prev ? (
        <NavItem
          href={`/blog/${prev.slug}`}
          title={prev.title}
          label="← 上一篇"
          direction="prev"
        />
      ) : (
        <div style={{ flex: 1 }} />
      )}

      {next ? (
        <NavItem
          href={`/blog/${next.slug}`}
          title={next.title}
          label="下一篇 →"
          direction="next"
        />
      ) : (
        <div style={{ flex: 1 }} />
      )}
    </nav>
  )
}
