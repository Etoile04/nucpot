import Link from "next/link"
import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "标准 / 规范 - NucPot",
  description:
    "NucPot 平台所遵循的数据元数据规范、势函数文件格式、引用与许可口径、参考的国际数据库。",
}

// NFM-4991 (IA-REF P2): new /about/standards sub-page (platform-design
// §1 站点地图 「关于 /about › 标准/规范」). The content here was previously
// scattered inline across /about landing sections; consolidating it here
// makes the standards source-of-truth reachable from a single URL and
// gives reviewers a stable target for citation.

interface StandardSection {
  readonly title: string
  readonly items: readonly { label: string; body: string }[]
}

const SECTIONS: readonly StandardSection[] = [
  {
    title: "元数据规范",
    items: [
      {
        label: "势函数元数据",
        body: "参考 OpenKIM 编辑器(EDN)字段模型。势函数条目包含名称、版本、势类型、代码族、元素覆盖、适用相、温压范围、辐照标签、质量等级与许可信息,与 OpenKIM Naming Convention 对齐。",
      },
      {
        label: "材料体系",
        body: "采用统一 slug 命名(U、U-Mo、UO₂、U-Zr、Zr、Zr-Nb、Fission Gas in Matrix 等),元素符号遵循 IUPAC,相结构标记保留平台既有的 JSONB 字段以兼容既有 GIN 索引。",
      },
      {
        label: "文献元数据",
        body: "DOI 优先,缺失时按 ADR-017 §2.5 使用 PDF 内容哈希(SHA-256)做次级去重。",
      },
    ],
  },
  {
    title: "文件格式",
    items: [
      {
        label: "势函数文件",
        body: "支持 EAM(.eam / .eam.fs / .eam.alloy / .alloy / .fs)、MEAM(.meam)、Tersoff / SW / BOP、MTP / DeePMD(.mtp / .pb / .pth)、ACE、ReaxFF(.reaxff)、COMB、LJ、Adp 等;扩展白名单见 apps/api/src/nfm_db/api/v1/potentials.py 中 upload_potential_file 的 allowed 集合。",
      },
      {
        label: "校验与下载",
        body: "上传后立即计算 SHA-256;下载 URL 走平台代理(见 NFM-4309 canonical URL 合同),禁止引用容器内路径。",
      },
    ],
  },
  {
    title: "引用与许可",
    items: [
      {
        label: "引用",
        body: "平台对收录势函数自动关联 primary / validation / application / review 四类文献,DOI 直接展示;详情页提供 BibTeX 友好链接以便外部引用。",
      },
      {
        label: "许可",
        body: "支持 open(开放)、author-specific(作者特定)两类;字段为 license + license_url;开放协议的势函数可在详情页直接下载,作者特定协议的下载需在页面确认许可条款。",
      },
    ],
  },
  {
    title: "国际数据库映射",
    items: [
      {
        label: "NIST IPR",
        body: "NucPot 的势函数元数据与 NIST Interatomic Potentials Repository 对齐,字段命名参考 NIST 公开 schema。",
      },
      {
        label: "OpenKIM",
        body: "元数据模型参考 OpenKIM Editor(EDN)字段,势函数命名遵循 OpenKIM Naming Convention。",
      },
    ],
  },
]

export default function StandardsPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12 space-y-10">
        <nav aria-label="面包屑" className="text-sm text-gray-400">
          <Link href="/about" className="hover:!text-blue-400 transition">
            关于
          </Link>
          <span className="mx-2 text-gray-600">/</span>
          <span className="text-gray-200">标准 / 规范</span>
        </nav>

        <header>
          <h1 className="text-3xl font-bold mb-3">标准 / 规范</h1>
          <p className="text-gray-300 leading-relaxed">
            NucPot 平台所遵循的元数据规范、势函数文件格式、引用与许可口径,
            以及与国际数据库的映射关系。本页内容是平台数据建模、收录与对外
            引用的口径基线,如需新增规范请联系{" "}
            <a
              href="mailto:liwenjie@npic.ac.cn"
              className="text-blue-400 hover:underline"
            >
              liwenjie@npic.ac.cn
            </a>
            。
          </p>
        </header>

        {SECTIONS.map((section) => (
          <section key={section.title}>
            <h2 className="text-2xl font-semibold mb-4 text-gray-100">
              {section.title}
            </h2>
            <dl className="space-y-4">
              {section.items.map((item) => (
                <div key={item.label} className="border-l-2 border-gray-700 pl-4">
                  <dt className="text-base font-semibold text-gray-100 mb-1">
                    {item.label}
                  </dt>
                  <dd className="text-sm text-gray-400 leading-relaxed">{item.body}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}

        <section className="rounded-lg border border-gray-700 bg-gray-800/40 p-5">
          <h2 className="text-lg font-semibold mb-2 text-gray-100">数据完整性声明</h2>
          <p className="text-sm text-gray-400 leading-relaxed mb-3">
            平台对每条势函数条目维护数据完整性元数据(字段完整性、文献关联、
            文件校验和),具体口径见{" "}
            <Link
              href="/about/data-integrity"
              className="text-blue-400 hover:underline"
            >
              数据完整性
            </Link>
            {" "}页面。
          </p>
          <p className="text-sm text-gray-400 leading-relaxed">
            API 字段定义见{" "}
            <Link href="/api-docs" className="text-blue-400 hover:underline">
              API 文档
            </Link>
            (基于 FastAPI 自动生成的 OpenAPI 3.1 规范)。
          </p>
        </section>
      </main>
    </div>
  )
}