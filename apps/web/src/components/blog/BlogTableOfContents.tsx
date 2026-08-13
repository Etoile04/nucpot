"use client"

import { useEffect, useState } from "react"
import { extractHeadings } from "@/lib/blog/headings"

interface BlogTableOfContentsProps {
  readonly content: string
}

export function BlogTableOfContents({ content }: BlogTableOfContentsProps) {
  const [activeId, setActiveId] = useState<string | null>(null)

  // Heading extraction is pure and only depends on the markdown source, which
  // is static at render time. Computing it inline avoids an extra render
  // cycle (and the old effect-based setState).
  const tocItems = extractHeadings(content)

  useEffect(() => {
    if (tocItems.length === 0) return

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

    tocItems.forEach((item) => {
      const element = document.getElementById(item.id)
      if (element) observer.observe(element)
    })

    return () => observer.disconnect()
  }, [tocItems])

  if (tocItems.length === 0) {
    return null
  }

  function scrollToHeading(itemId: string, itemText: string): void {
    const direct = document.getElementById(itemId)
    if (direct) {
      direct.scrollIntoView({ behavior: "smooth" })
      setActiveId(itemId)
      return
    }

    // Fallback for headings whose id wasn't applied at render time. Locate
    // the heading by matching textContent, then inject the id and scroll.
    const headings = document.querySelectorAll(
      ".blog-prose h1, .blog-prose h2, .blog-prose h3, .blog-prose h4, .blog-prose h5, .blog-prose h6"
    )
    for (const heading of Array.from(headings)) {
      if (heading.textContent?.trim() === itemText) {
        heading.id = itemId
        heading.scrollIntoView({ behavior: "smooth" })
        setActiveId(itemId)
        return
      }
    }
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
                    scrollToHeading(item.id, item.text)
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
