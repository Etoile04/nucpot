"use client"

import Link from "next/link"
import { useState } from "react"

export default function HomePage() {
  const [isHovered, setIsHovered] = useState(false)

  return (
    <main
      style={{
        maxWidth: 960,
        margin: "0 auto",
        padding: "4rem 1.5rem",
      }}
    >
      <h1
        style={{
          fontSize: "2.5rem",
          fontWeight: 700,
          marginBottom: "1rem",
          letterSpacing: "-0.02em",
        }}
      >
        核燃料与材料物性数据库
      </h1>
      <p
        style={{
          fontSize: "1.125rem",
          color: "var(--color-text-secondary)",
          marginBottom: "0.5rem",
        }}
      >
        可持续共享的核燃料与材料物性数据库平台
      </p>
      <p
        style={{
          fontSize: "1rem",
          color: "#666",
          marginBottom: "3rem",
        }}
      >
        Nuclear Fuel &amp; Materials Properties Database — a sustainable and
        sharing platform for nuclear materials data in China.
      </p>

      <section
        style={{
          padding: "2rem",
          background: "var(--color-surface-elevated)",
          border: "1px solid var(--color-border)",
          borderRadius: 8,
          marginBottom: "2rem",
        }}
      >
        <h2
          style={{
            fontSize: "1.5rem",
            fontWeight: 600,
            marginBottom: "1rem",
          }}
        >
          技术文档
        </h2>
        <p
          style={{
            fontSize: "1rem",
            color: "var(--color-text-secondary)",
            marginBottom: "1.5rem",
            lineHeight: 1.6,
          }}
        >
          查看使用指南、API 文档和核材料科学领域的技术文章
        </p>
        <Link
          href="/blog"
          style={{
            display: "inline-block",
            padding: "0.75rem 1.5rem",
            fontSize: "1rem",
            fontWeight: 500,
            color: "#fff",
            background: isHovered ? "#40a9ff" : "#1890ff",
            border: "none",
            borderRadius: 4,
            textDecoration: "none",
            transition: "background-color 150ms ease",
          }}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
        >
          查看技术博客 →
        </Link>
      </section>
    </main>
  )
}
