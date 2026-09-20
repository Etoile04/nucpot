import Link from "next/link"
import { getAllPosts } from "@/lib/blog/posts"
import { formatDate } from "@/lib/blog/format-date"
import { getHomeData } from "@/lib/home/home-data"
import { HeroSearch } from "@/components/home/HeroSearch"
import { StatsStrip } from "@/components/home/StatsStrip"
import { PopularPotentials } from "@/components/home/PopularPotentials"
import { RecentUpdates } from "@/components/home/RecentUpdates"
import { SystemNav } from "@/components/home/SystemNav"

const LATEST_POSTS_COUNT = 3

// NFM-4990: hero quick entries follow the P1 primary IA (势函数列表 /
// 材料体系 / 文献库) plus the compare tool as the fourth destination.
// Benchmark / 数据集 / API 文档 are unbuilt blocks and intentionally
// absent (P2 enables them).
const QUICK_ENTRIES: readonly { href: string; title: string; description: string }[] = [
  { href: "/potentials", title: "浏览势函数", description: "按元素、函数形式筛选与检索" },
  { href: "/materials", title: "材料体系", description: "核燃料与结构材料数据" },
  { href: "/publications", title: "文献库", description: "文献管理与提取结果" },
  { href: "/potentials/compare", title: "势函数对比", description: "并排比较多条势函数" },
]

// Real counts only (统计数据与 API 一致): render per-request so the
// numbers can never be a stale build-time bake. Sections are fail-soft.
export const dynamic = "force-dynamic"

export default async function HomePage() {
  const [homeData, allPosts] = await Promise.all([getHomeData(), Promise.resolve(getAllPosts())])
  const latestPosts = allPosts.slice(0, LATEST_POSTS_COUNT)

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-12">
      {/* ① Hero — 平台定位 + 搜索框 + 快捷入口 (NFM-1064 §3.1) */}
      <section aria-labelledby="home-hero-heading" className="mb-14 text-center">
        <h1 id="home-hero-heading" className="text-4xl font-bold mb-4 tracking-tight">
          核燃料与材料物性数据库
        </h1>
        <p className="text-lg text-gray-300 mb-2">可持续共享的核燃料与材料物性数据库平台</p>
        <p className="text-sm text-gray-400 mb-8">
          Nuclear Fuel &amp; Materials Properties Database — interatomic potentials,
          materials data and literature for nuclear materials research.
        </p>
        <div className="flex justify-center mb-10">
          <HeroSearch />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-left">
          {QUICK_ENTRIES.map((entry) => (
            <Link
              key={entry.href}
              href={entry.href}
              className="block p-5 rounded-lg transition-colors duration-150 hover:border-blue-400"
              style={{
                background: "var(--color-surface)",
                border: "1px solid var(--color-border)",
              }}
            >
              <div className="text-base font-semibold text-gray-100 mb-1">{entry.title}</div>
              <div className="text-sm text-gray-400">{entry.description}</div>
            </Link>
          ))}
        </div>
      </section>

      {/* ② 统计数据区 */}
      <StatsStrip stats={homeData.stats} />

      {/* ③ 热门势函数（下载量排序，8 卡） */}
      {homeData.popular !== null && homeData.popular.length > 0 && (
        <PopularPotentials potentials={homeData.popular} />
      )}

      {/* ④ 最新更新区 */}
      {homeData.recent !== null && homeData.recent.length > 0 && (
        <RecentUpdates items={homeData.recent} />
      )}

      {/* ⑤ 体系导航区（四大体系分组） */}
      {homeData.systems !== null && <SystemNav groups={homeData.systems} />}

      {latestPosts.length > 0 && (
        <section aria-labelledby="home-posts-heading" className="mt-14">
          <h2 id="home-posts-heading" className="text-2xl font-semibold mb-6">
            最新文章
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {latestPosts.map((post) => (
              <Link
                key={post.slug}
                href={`/blog/${post.slug}`}
                className="block p-5 bg-gray-700 border border-gray-600 rounded-lg hover:border-blue-400 transition-colors duration-150"
              >
                <h3 className="text-lg font-semibold mb-2 text-gray-100 leading-snug line-clamp-2">
                  {post.title}
                </h3>
                <time
                  dateTime={post.date}
                  className="block text-sm text-gray-300 mb-2"
                >
                  {formatDate(post.date)}
                </time>
                <p className="text-sm text-gray-300 leading-relaxed line-clamp-3 mb-3">
                  {post.summary}
                </p>
                {post.tags.length > 0 && (
                  <span className="inline-block px-2 py-0.5 text-xs rounded bg-gray-600 text-gray-300">
                    {post.tags[0]}
                  </span>
                )}
              </Link>
            ))}
          </div>
        </section>
      )}
    </main>
  )
}
