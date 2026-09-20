import Link from "next/link"
import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "关于 - NucPot",
  description:
    "NucPot 核材料势函数开放平台:项目背景、数据来源、平台遵循的标准与规范、贡献指南与协作团队。",
}

// NFM-4991 (IA-REF P2): refactored /about to a landing page that matches
// platform-design.md §1 站点地图 — the three first-level sub-entries below
// (标准/规范, 贡献指南, 团队) are now real routes under /about/* rather
// than inline sections. Sections previously inlined (项目背景, 数据来源,
// 联系方式) stay on this landing so the P1 link graph doesn't change.
//
// The three child routes each have their own page.tsx in
// apps/web/src/app/about/{standards,contribute,team}/page.tsx.

interface SubCard {
  readonly href: "/about/standards" | "/about/contribute" | "/about/team"
  readonly title: string
  readonly description: string
}

const SUB_CARDS: readonly SubCard[] = [
  {
    href: "/about/standards",
    title: "标准 / 规范",
    description:
      "平台数据所遵循的元数据规范、势函数格式、引用与许可口径,以及与国际数据库的映射关系。",
  },
  {
    href: "/about/contribute",
    title: "贡献指南",
    description:
      "势函数与文献数据的提交、审核、版本与发布流程;数据完整性与署名归属要求。",
  },
  {
    href: "/about/team",
    title: "团队",
    description:
      "平台维护团队、协作单位、贡献者与对外联系方式。",
  },
]

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12 space-y-12">
        {/* Section 1: 项目背景 */}
        <section>
          <h1 className="text-3xl font-bold mb-4">关于核材料势函数库</h1>
          <p className="text-gray-300 text-lg leading-relaxed mb-6">
            面向核燃料、包壳和结构材料的原子间势函数开放平台。致力于为核材料研究者提供可靠的势函数存储、检索与共享服务。
          </p>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              覆盖金属燃料(U-Zr、U-Mo)、氧化物燃料(UO₂)、包壳材料(Zr、Zr-Nb)、结构材料(Fe)
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              支持经典势(EAM、MEAM)和机器学习势(RANN)
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              元数据参考 OpenKIM EDN 标准
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              与主流模拟软件(LAMMPS、GULP)兼容
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        {/* Section 2: 数据来源 */}
        <section>
          <h2 className="text-2xl font-semibold mb-4">数据来源</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              NIST Interatomic Potentials Repository (IPR)
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              OpenKIM
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              开发者直接贡献
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        {/* NFM-4991: 3 sub-section cards — previously inline 协作团队 /
            致谢 / 联系方式 sections now live at /about/team,
            /about/contribute, /about/standards respectively. Each card
            is a real route so deep links are shareable and the layout
            scales beyond a single column. */}
        <section aria-labelledby="about-subsections-heading">
          <h2 id="about-subsections-heading" className="text-2xl font-semibold mb-4">
            关于本平台
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {SUB_CARDS.map((card) => (
              <Link
                key={card.href}
                href={card.href}
                className="block p-5 rounded-lg transition-colors duration-150 hover:border-blue-400"
                style={{
                  background: "var(--color-surface)",
                  border: "1px solid var(--color-border)",
                }}
              >
                <div className="text-base font-semibold text-gray-100 mb-1">
                  {card.title}
                </div>
                <p className="text-sm text-gray-400 leading-relaxed">
                  {card.description}
                </p>
              </Link>
            ))}
          </div>
        </section>

        <hr className="border-gray-700" />

        {/* 致谢 */}
        <section>
          <h2 className="text-2xl font-semibold mb-4">致谢</h2>
          <p className="text-gray-300 leading-relaxed">
            感谢中核集团焦拥军首席专家提议建设开源势函数网站。
          </p>
        </section>

        <hr className="border-gray-700" />

        {/* Section 4: 联系方式 */}
        <section>
          <h2 className="text-2xl font-semibold mb-4">联系方式</h2>
          <p className="text-gray-300 mb-3">如有问题或合作意向，请通过以下方式联系：</p>
          <p className="text-gray-400">
            📧&nbsp;
            <a href="mailto:liwenjie@npic.ac.cn" className="text-blue-400 hover:underline">
              liwenjie@npic.ac.cn
            </a>
          </p>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-700 px-6 py-6 text-center text-sm text-gray-500">
        NucPot 核材料势函数库 · 面向核材料研究的开放平台
      </footer>
    </div>
  )
}