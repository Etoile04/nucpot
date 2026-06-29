"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

interface BreadcrumbItem {
  readonly name: string
  readonly href: string
}

export function BlogBreadcrumb() {
  const pathname = usePathname()

  // Build breadcrumb items based on current path
  const items: BreadcrumbItem[] = [
    { name: "首页", href: "/" },
    { name: "技术博客", href: "/blog" },
  ]

  // Add current page if we're on a blog post
  if (pathname && pathname.startsWith("/blog/") && pathname !== "/blog") {
    items.push({ name: "文章详情", href: pathname })
  }

  if (items.length <= 1) {
    return null
  }

  return (
    <nav className="blog-breadcrumb" aria-label="面包屑导航">
      <ol className="blog-breadcrumb-list">
        {items.map((item, index) => (
          <li key={item.href} className="blog-breadcrumb-item">
            {index === items.length - 1 ? (
              <span className="blog-breadcrumb-current">{item.name}</span>
            ) : (
              <>
                <Link href={item.href} className="blog-breadcrumb-link">
                  {item.name}
                </Link>
                <span className="blog-breadcrumb-separator">/</span>
              </>
            )}
          </li>
        ))}
      </ol>
    </nav>
  )
}
