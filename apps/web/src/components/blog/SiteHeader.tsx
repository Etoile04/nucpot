"use client"

import Link from "next/link"

interface NavItem {
  label: string
  href: string
  ariaLabel?: string
}

// NFM-4990 (IA-REFACTOR P1): first-level entries mirror the Nav.tsx
// primary set from the NFM-4984 site-map ruling, plus 博客 itself — this
// header belongs to the blog surface. Retired first-level destinations
// (检索/对比/本体/反馈) stay reachable via the global Nav 更多 menu.
const mainNavigation: NavItem[] = [
  { label: "势函数列表", href: "/potentials", ariaLabel: "浏览势函数数据" },
  { label: "材料体系", href: "/materials", ariaLabel: "材料体系库" },
  { label: "文献库", href: "/publications", ariaLabel: "文献库" },
  { label: "博客", href: "/blog", ariaLabel: "技术博客文章" },
  { label: "关于", href: "/about", ariaLabel: "关于我们" },
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
