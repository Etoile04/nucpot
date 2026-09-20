import type { HomeStats } from "@/lib/home/home-data"

/**
 * 统计数据区 (NFM-4990 首页五要素 §2).
 *
 * Every number comes from a live public endpoint (home-data.ts) — a
 * failed source renders 「—」, never a fabricated or baked-in value, per
 * the acceptance criterion 统计数与 API 一致.
 *
 * Card set note: the ruling's draft mentioned a "benchmark 条目数" card,
 * but the Benchmark block is explicitly unbuilt in P1 (no public
 * benchmark endpoint exists — see NFM-4984 裁决 2) and rendering a dead
 * stat would violate the 未建区块不渲染 / 无死链 acceptance. The four
 * cards below are the real public counts the backend supports today.
 */
const CARDS: readonly { key: keyof HomeStats; label: string; unit: string }[] = [
  { key: "potentials", label: "势函数", unit: "条" },
  { key: "materials", label: "材料条目", unit: "条" },
  { key: "literature", label: "文献", unit: "篇" },
  { key: "measurements", label: "物性测量", unit: "条" },
]

function formatCount(value: number | null): string {
  if (value === null) return "—"
  return value.toLocaleString("zh-CN")
}

export function StatsStrip({ stats }: { stats: HomeStats }) {
  return (
    <section aria-labelledby="home-stats-heading" className="mb-14">
      <h2 id="home-stats-heading" className="text-2xl font-semibold mb-6">
        数据规模
      </h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {CARDS.map((card) => (
          <div
            key={card.key}
            className="p-6 rounded-lg text-center"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
            }}
          >
            <div className="text-3xl font-bold tabular-nums" style={{ color: "var(--color-text)" }}>
              {formatCount(stats[card.key])}
            </div>
            <div className="mt-2 text-sm" style={{ color: "var(--color-text-secondary)" }}>
              {card.label}
              {stats[card.key] === null ? "（暂不可用）" : ` ${card.unit}`}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
