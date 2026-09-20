import Link from "next/link"
import type { HomeSystemGroup } from "@/lib/home/home-data"

/**
 * 体系导航区 (NFM-4990 首页五要素 §5).
 *
 * The four ruling-mandated groups (金属燃料 · 氧化物燃料 · 包壳 · 裂变气体).
 * hrefs are resolved server-side in home-data.ts from the live
 * material-categories list (slug → /materials?category_id=…), so a new
 * category mapping never requires a frontend deploy.
 */
export function SystemNav({ groups }: { groups: readonly HomeSystemGroup[] }) {
  return (
    <section aria-labelledby="home-systems-heading" className="mb-14">
      <div className="flex items-baseline justify-between mb-6">
        <h2 id="home-systems-heading" className="text-2xl font-semibold">
          体系导航
        </h2>
        <Link href="/materials" className="text-sm text-blue-400 hover:text-blue-300">
          全部材料体系 →
        </Link>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {groups.map((group) => (
          <Link
            key={group.key}
            href={group.href}
            className="block p-6 rounded-lg transition-colors duration-150 hover:border-blue-400"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
            }}
          >
            <h3 className="text-lg font-semibold text-gray-100 mb-2">{group.title}</h3>
            <p className="text-sm text-gray-400 leading-relaxed">{group.description}</p>
          </Link>
        ))}
      </div>
    </section>
  )
}
