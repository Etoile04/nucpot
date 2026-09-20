import Link from "next/link"
import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "团队 - NucPot",
  description:
    "NucPot 核材料势函数平台的维护团队、协作单位与对外联系方式。",
}

// NFM-4991 (IA-REF P2): new /about/team sub-page (platform-design §1
// 站点地图 「关于 /about › 团队」). The 协作团队 inline section that
// previously lived on /about/page.tsx moved here verbatim and gains a
// canonical shareable URL.

interface TeamEntry {
  readonly name: string
  readonly role: string
  readonly affiliation: string
}

const MAINTAINERS: readonly TeamEntry[] = [
  {
    name: "李文杰",
    role: "项目开发与维护 / NucPot 主开发者",
    affiliation: "核动力院",
  },
]

const COLLABORATORS: readonly TeamEntry[] = [
  {
    name: "邓辉球团队",
    role: "势函数梳理与设计",
    affiliation: "湖南大学",
  },
  {
    name: "核动力院",
    role: "核心协作方",
    affiliation: "中国核动力研究设计院",
  },
]

const ADVISORS: readonly TeamEntry[] = [
  {
    name: "焦拥军",
    role: "首席专家 / 平台立项提议人",
    affiliation: "中核集团",
  },
]

export default function TeamPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12 space-y-10">
        <nav aria-label="面包屑" className="text-sm text-gray-400">
          <Link href="/about" className="hover:!text-blue-400 transition">
            关于
          </Link>
          <span className="mx-2 text-gray-600">/</span>
          <span className="text-gray-200">团队</span>
        </nav>

        <header>
          <h1 className="text-3xl font-bold mb-3">团队</h1>
          <p className="text-gray-300 leading-relaxed">
            NucPot 平台由以下团队与个人协作维护。如希望加入协作,请通过{" "}
            <Link href="/feedback" className="text-blue-400 hover:underline">
              反馈页
            </Link>
            {" "}或邮件联系。
          </p>
        </header>

        <TeamGroup title="平台维护" entries={MAINTAINERS} />
        <TeamGroup title="协作团队" entries={COLLABORATORS} />
        <TeamGroup title="专家顾问" entries={ADVISORS} />

        <section className="rounded-lg border border-gray-700 bg-gray-800/40 p-5">
          <h2 className="text-lg font-semibold mb-2 text-gray-100">联系方式</h2>
          <p className="text-sm text-gray-400 leading-relaxed mb-3">
            合作意向与学术联系:{" "}
            <a
              href="mailto:liwenjie@npic.ac.cn"
              className="text-blue-400 hover:underline"
            >
              liwenjie@npic.ac.cn
            </a>
          </p>
          <p className="text-sm text-gray-400 leading-relaxed">
            贡献入口与技术反馈见{" "}
            <Link href="/about/contribute" className="text-blue-400 hover:underline">
              贡献指南
            </Link>
            。
          </p>
        </section>
      </main>
    </div>
  )
}

interface TeamGroupProps {
  readonly title: string
  readonly entries: readonly TeamEntry[]
}

function TeamGroup({ title, entries }: TeamGroupProps) {
  return (
    <section>
      <h2 className="text-2xl font-semibold mb-4 text-gray-100">{title}</h2>
      <ul className="space-y-3">
        {entries.map((entry) => (
          <li
            key={`${entry.name}-${entry.affiliation}`}
            className="border-l-2 border-gray-700 pl-4"
          >
            <div className="text-base font-semibold text-gray-100">
              {entry.name}
              <span className="ml-2 text-xs text-gray-500 font-normal">
                {entry.affiliation}
              </span>
            </div>
            <p className="text-sm text-gray-400 mt-1">{entry.role}</p>
          </li>
        ))}
      </ul>
    </section>
  )
}