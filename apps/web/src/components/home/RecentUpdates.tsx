import Link from "next/link"
import type { HomeRecentPotential } from "@/lib/home/home-data"

/**
 * 最新更新区 (NFM-4990 首页五要素 §4).
 *
 * Rows come straight from /api/v1/stats recent_potentials — the same
 * list the backend already maintains; no re-derivation client-side.
 */

function formatDate(iso: string | null): string {
  if (!iso) return ""
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return ""
  return parsed.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" })
}

export function RecentUpdates({ items }: { items: readonly HomeRecentPotential[] }) {
  return (
    <section aria-labelledby="home-recent-heading" className="mb-14">
      <h2 id="home-recent-heading" className="text-2xl font-semibold mb-6">
        最新更新
      </h2>
      <ul
        className="rounded-lg divide-y divide-gray-700"
        style={{ background: "var(--color-surface)" }}
      >
        {items.map((item) => (
          <li key={item.id}>
            <Link
              href={`/potentials/${item.id}`}
              className="flex flex-wrap items-center justify-between gap-2 px-5 py-4 hover:bg-gray-700/40 transition-colors duration-150"
            >
              <span className="min-w-0">
                <span className="text-sm font-medium text-gray-100">
                  {item.displayName || item.name}
                </span>
                {item.elements.length > 0 && (
                  <span className="ml-3 text-xs text-gray-400">{item.elements.join("-")}</span>
                )}
              </span>
              <span className="flex items-center gap-3 text-xs text-gray-400">
                <span>{item.type}</span>
                {formatDate(item.createdAt) && <time dateTime={item.createdAt ?? undefined}>{formatDate(item.createdAt)}</time>}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
