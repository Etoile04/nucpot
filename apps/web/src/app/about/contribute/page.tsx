import Link from "next/link"
import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "贡献指南 - NucPot",
  description:
    "NucPot 平台的势函数与文献数据提交、审核、版本与发布流程;数据完整性与署名归属要求。",
}

// NFM-4991 (IA-REF P2): new /about/contribute sub-page (platform-design
// §1 站点地图 「关于 /about › 贡献指南」). This page documents the
// human-readable workflow; the actual submission UI is gated behind
// authentication at /upload (authenticated users with the editor role).

interface WorkflowStep {
  readonly step: number
  readonly title: string
  readonly body: string
}

const WORKFLOW: readonly WorkflowStep[] = [
  {
    step: 1,
    title: "准备数据",
    body: "整理势函数文件(LAMMPS / GULP / ASE 等主流格式)、原始论文 PDF 与必要的元数据(元素、体系、温压范围、适用相、辐照标签、质量等级)。建议在提交前阅读 /about/standards 确认口径一致。",
  },
  {
    step: 2,
    title: "注册并申请编辑权限",
    body: "使用机构邮箱在 /login 注册账号;新用户默认只读。如需提交数据,请通过 /feedback 提交申请,管理员审核后授予 editor 角色(约 1-3 个工作日)。",
  },
  {
    step: 3,
    title: "上传势函数与文献",
    body: "登录后进入 /upload,选择「势函数」或「文献」分步向导;系统会引导填写元数据、上传文件、关联文献 DOI。文件上传后可在 /pub 上修改元数据。",
  },
  {
    step: 4,
    title: "审核与发布",
    body: "提交后进入审核队列(管理员 + editor 可见);流程包括初审(格式/必填项)、技术审查(物理合理性、参数一致性)、发布(状态由 draft → review → published)。每次状态变更均有审计日志。",
  },
  {
    step: 5,
    title: "版本与废弃",
    body: "对已发布势函数的修订请新增版本(slug 不变、version +1),保留旧版本以便复现实验;若势函数被原作者废弃,请使用 deprecated 状态而非删除。",
  },
]

interface DataAttribution {
  readonly title: string
  readonly body: string
}

const ATTRIBUTION: readonly DataAttribution[] = [
  {
    title: "贡献者署名",
    body: "每条势函数条目自动记录贡献者 ID(关联 users 表),并在详情页展示;文献关联会保留 primary / validation / application / review 四类关系。",
  },
  {
    title: "引用规范",
    body: "引用本平台数据时,请同时引用原始论文 DOI 与本平台条目 slug;详细引用模板见 /about/standards。",
  },
  {
    title: "撤回与勘误",
    body: "如发现已发布数据存在错误,请通过 /feedback 或邮件联系;管理员会先标记 deprecated,再与原作者协同修订后重新发布。",
  },
]

export default function ContributePage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12 space-y-10">
        <nav aria-label="面包屑" className="text-sm text-gray-400">
          <Link href="/about" className="hover:!text-blue-400 transition">
            关于
          </Link>
          <span className="mx-2 text-gray-600">/</span>
          <span className="text-gray-200">贡献指南</span>
        </nav>

        <header>
          <h1 className="text-3xl font-bold mb-3">贡献指南</h1>
          <p className="text-gray-300 leading-relaxed">
            NucPot 平台接受核材料势函数、文献元数据、Benchmark 数据的贡献。
            所有贡献均经过审核后发布,并保留完整的版本与署名记录以支持可复现
            研究。
          </p>
        </header>

        <section>
          <h2 className="text-2xl font-semibold mb-4 text-gray-100">提交流程</h2>
          <ol className="space-y-5">
            {WORKFLOW.map((step) => (
              <li key={step.step} className="flex gap-4">
                <span
                  className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold"
                  style={{
                    background: "var(--color-accent, #60a5fa)",
                    color: "#0f172a",
                  }}
                  aria-hidden="true"
                >
                  {step.step}
                </span>
                <div>
                  <h3 className="text-base font-semibold text-gray-100 mb-1">
                    {step.title}
                  </h3>
                  <p className="text-sm text-gray-400 leading-relaxed">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section>
          <h2 className="text-2xl font-semibold mb-4 text-gray-100">数据完整性与署名</h2>
          <div className="space-y-4">
            {ATTRIBUTION.map((item) => (
              <div key={item.title} className="border-l-2 border-gray-700 pl-4">
                <h3 className="text-base font-semibold text-gray-100 mb-1">
                  {item.title}
                </h3>
                <p className="text-sm text-gray-400 leading-relaxed">{item.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-lg border border-gray-700 bg-gray-800/40 p-5">
          <h2 className="text-lg font-semibold mb-2 text-gray-100">需要帮助?</h2>
          <p className="text-sm text-gray-400 leading-relaxed mb-3">
            如在提交过程中遇到问题,请通过{" "}
            <Link href="/feedback" className="text-blue-400 hover:underline">
              反馈页
            </Link>
            {" "}提交工单,或直接邮件联系{" "}
            <a
              href="mailto:liwenjie@npic.ac.cn"
              className="text-blue-400 hover:underline"
            >
              liwenjie@npic.ac.cn
            </a>
            。常见问题汇总见 API 文档页右上角的 OpenAPI 规范。
          </p>
          <p className="text-sm text-gray-400 leading-relaxed">
            平台的字段定义、错误响应与速率限制见{" "}
            <Link href="/api-docs" className="text-blue-400 hover:underline">
              API 文档
            </Link>
            。
          </p>
        </section>
      </main>
    </div>
  )
}