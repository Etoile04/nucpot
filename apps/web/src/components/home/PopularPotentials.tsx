import Link from "next/link"
import type { HomePopularPotential } from "@/lib/home/home-data"

/**
 * 热门势函数区 (NFM-4990 首页五要素 §3).
 *
 * Up to 8 cards ranked by the NFM-4309 download counter
 * (/api/v1/potentials?sort=downloads). Views/citations are not tracked
 * by the backend, so download count is the only real popularity signal
 * rendered — no synthesized ranking.
 *
 * Type color coding follows NFM-1064 §2.1 (EAM/MEAM/MTP/ACE/LJ).
 */
const TYPE_TEXT_COLOR: Readonly<Record<string, string>> = {
  EAM: "text-blue-400",
  MEAM: "text-green-400",
  MTP: "text-purple-400",
  ACE: "text-orange-400",
  LJ: "text-cyan-400",
}

export function PopularPotentials({ potentials }: { potentials: readonly HomePopularPotential[] }) {
  return (
    <section aria-labelledby="home-popular-heading" className="mb-14">
      <div className="flex items-baseline justify-between mb-6">
        <h2 id="home-popular-heading" className="text-2xl font-semibold">
          热门势函数
        </h2>
        <Link href="/potentials" className="text-sm text-blue-400 hover:text-blue-300">
          查看全部 →
        </Link>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {potentials.map((p) => (
          <Link
            key={p.id}
            href={`/potentials/${p.id}`}
            className="block p-5 rounded-lg transition-colors duration-150 hover:border-blue-400 h-full"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
            }}
          >
            <div className="flex items-center justify-between gap-2 mb-2">
              <span
                className={`text-xs font-semibold ${TYPE_TEXT_COLOR[p.type] ?? "text-gray-300"}`}
              >
                {p.type}
              </span>
              <span className="text-xs text-gray-400 tabular-nums">{p.downloadCount} 次下载</span>
            </div>
            <h3 className="text-base font-semibold leading-snug line-clamp-2 text-gray-100 mb-2">
              {p.displayName || p.name}
            </h3>
            {p.elements.length > 0 && (
              <p className="text-sm text-gray-400 mb-2">{p.elements.join(" · ")}</p>
            )}
            {p.description && (
              <p className="text-sm text-gray-400 leading-relaxed line-clamp-2">{p.description}</p>
            )}
          </Link>
        ))}
      </div>
    </section>
  )
}
