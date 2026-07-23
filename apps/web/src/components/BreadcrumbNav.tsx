/**
 * BreadcrumbNav — automatic pathname-based breadcrumb navigation.
 *
 * Parses the current URL pathname into a hierarchy of clickable
 * segments. The last segment is rendered as plain text (current page),
 * all others link back to their route.
 *
 * Optionally accepts an `items` prop for fully custom breadcrumbs
 * (useful when route segments don't match display labels).
 */

'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

// ── Types ─────────────────────────────────────────────────────────────

export interface BreadcrumbItem {
  readonly label: string
  readonly href: string
}

interface BreadcrumbNavProps {
  /**
   * Fully override auto-parsed breadcrumbs with explicit items.
   * The last item's label is rendered as plain text (no link).
   */
  readonly items?: readonly BreadcrumbItem[]
}

// ── Pathname → label mapping ───────────────────────────────────────────

const PATH_LABELS: Record<string, string> = {
  kg: '知识图谱',
  nodes: '节点详情',
  explore: '图谱浏览',
  materials: '材料库',
  graph: '材料图谱',
  properties: '材料属性',
  literature: '文献管理',
}

function segmentLabel(segment: string): string {
 return PATH_LABELS[segment] ?? decodeURIComponent(segment)
}

// ── Auto-parse pathname into breadcrumb items ──────────────────────────

function parsePathname(pathname: string): BreadcrumbItem[] {
  const segments = pathname
    .split('/')
    .filter((s) => s.length > 0)

  if (segments.length === 0) return []

  const items: BreadcrumbItem[] = [{ label: '首页', href: '/' }]

  let href = ''
  for (let i = 0; i < segments.length; i++) {
    href += '/' + segments[i]
    items.push({ label: segmentLabel(segments[i]!), href })
  }

  return items
}

// ── Component ──────────────────────────────────────────────────────────

export function BreadcrumbNav({ items }: BreadcrumbNavProps) {
  const pathname = usePathname()
  const crumbs = items ?? parsePathname(pathname)

  if (crumbs.length <= 1) return null

  const links = crumbs.slice(0, -1)
  const current = crumbs[crumbs.length - 1]!

  return (
    <nav
      aria-label="Breadcrumb"
      className="px-4 py-2"
    >
      <ol className="flex flex-wrap items-center gap-1 text-sm text-gray-400">
        {links.map((item) => (
          <li key={item.href} className="flex items-center gap-1">
            <Link
              href={item.href}
              className="hover:text-blue-400 transition-colors"
            >
              {item.label}
            </Link>
            <span aria-hidden="true" className="text-gray-600">
              &gt;
            </span>
          </li>
        ))}
        <li>
          <span className="text-gray-300 font-medium">
            {current.label}
          </span>
        </li>
      </ol>
    </nav>
  )
}
