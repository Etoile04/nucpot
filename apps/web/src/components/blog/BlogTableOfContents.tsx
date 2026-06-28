"use client"

import { useEffect, useState } from "react"

interface TocItem {
  readonly id: string
  readonly text: string
  readonly level: number
}

interface BlogTableOfContentsProps {
  readonly content: string
}

export function BlogTableOfContents({ content }: BlogTableOfContentsProps) {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [tocItems, setTocItems] = useState<readonly TocItem[]>([])

  useEffect(() => {
    // Extract headings from markdown content
    const headingRegex = /^(#{1,6})\s+(.+)$/gm
    const items: TocItem[] = []
    let match

    while ((match = headingRegex.exec(content)) !== null) {
      const level = match[1]?.length ?? 1
      const text = match[2]?.trim() ?? ""
      const id = text
        .toLowerCase()
        .replace(/[^\w\s-]/g, "")
        .replace(/\s+/g, "-")

      items.push({ id, text, level })
    }

    setTocItems(items)

    // Set up scroll spy
    if (items.length > 0) {
      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              setActiveId(entry.target.id)
            }
          })
        },
        { rootMargin: "-100px 0px -66%" }
      )

      // Observe all headings
      items.forEach((item) => {
        const element = document.getElementById(item.id)
        if (element) observer.observe(element)
      })

      return () => observer.disconnect()
    }
  }, [content])

  if (tocItems.length === 0) {
    return null
  }

  return (
    <aside className="blog-toc" aria-label="目录">
      <div className="blog-toc-content">
        <h3 className="blog-toc-title">目录</h3>
        <nav>
          <ul className="blog-toc-list">
            {tocItems.map((item) => (
              <li
                key={item.id}
                className="blog-toc-item"
                style={{ paddingLeft: `${(item.level - 1) * 1}rem` }}
              >
                <a
                  href={`#${item.id}`}
                  className={`blog-toc-link ${
                    activeId === item.id ? "blog-toc-link-active" : ""
                  }`}
                  onClick={(e) => {
                    e.preventDefault()
                    const element = document.getElementById(item.id)
                    if (element) {
                      element.scrollIntoView({ behavior: "smooth" })
                      setActiveId(item.id)
                    }
                  }}
                >
                  {item.text}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </aside>
  )
}
