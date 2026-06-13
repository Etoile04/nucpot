"use client"

import Link from "next/link"

interface NavItem {
  label: string
  href: string
  ariaLabel?: string
}

const mainNavigation: NavItem[] = [
  { label: "浏览", href: "/browse", ariaLabel: "浏览数据" },
  { label: "高级检索", href: "/search", ariaLabel: "高级检索功能" },
  { label: "对比", href: "/compare", ariaLabel: "对比材料数据" },
  { label: "反馈", href: "/feedback", ariaLabel: "提供反馈" },
  { label: "关于", href: "/about", ariaLabel: "关于我们" },
  { label: "博客", href: "/blog", ariaLabel: "技术博客文章" },
]

export function SiteHeader() {
  return (
    <header
      style={{
        borderBottom: "1px solid var(--color-border)",
        background: "var(--color-surface)",
        position: "sticky",
        top: 0,
        zIndex: 100,
      }}
    >
      <div
        style={{
          maxWidth: "var(--max-width)",
          margin: "0 auto",
          padding: "0.75rem 1.5rem",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Link
          href="/"
          style={{
            textDecoration: "none",
            color: "var(--color-text)",
            fontSize: "1.125rem",
            fontWeight: 600,
          }}
        >
          核燃料与材料物性数据库
        </Link>

        <nav aria-label="主导航" style={{ display: "flex", gap: "1.5rem" }}>
          {mainNavigation.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-label={item.ariaLabel}
              style={{
                textDecoration: "none",
                color: "var(--color-text-secondary)",
                fontSize: "0.9375rem",
                transition: "color 150ms ease",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--color-accent)"
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--color-text-secondary)"
              }}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  )
}
