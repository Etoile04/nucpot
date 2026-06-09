import Link from "next/link"

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

        <Link
          href="/blog"
          style={{
            textDecoration: "none",
            color: "var(--color-accent)",
            fontSize: "0.9375rem",
          }}
        >
          技术博客
        </Link>
      </div>
    </header>
  )
}
