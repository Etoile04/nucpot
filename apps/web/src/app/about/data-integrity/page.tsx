/**
 * /about/data-integrity — data-integrity disclosure stub (NFM-4236).
 *
 * Spec NFM-4134.A §8 #4: the "Learn more" link on the inline
 * `<DataLossNotice>` points here, and this page is to ship as a stub
 * in the same PR as the notice. Final copy is CTO-owned (factual
 * post-mortem); the facts below are pinned from the incident record
 * (NFM-4130 → NFM-4133 decision → NFM-4139 restoration migration).
 *
 * Style matches the existing /about page (dark surface, zh-CN copy).
 */
export default function DataIntegrityPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12 space-y-12">
        <section>
          <h1 className="text-3xl font-bold mb-4">数据完整性说明</h1>
          <p className="text-gray-300 text-lg leading-relaxed mb-6">
            本页说明 2026-09-02 数据源清理（迁移 070）对部分物性数据来源标注的影响。
            我们以脚注的方式在受影响的测量行上作出披露，而不是隐藏这一限制。
          </p>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">发生了什么</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              2026-09-02，迁移 070（提交 444469cda）合并了 18 条占位
              <span className="font-mono text-gray-300"> data_sources </span>
              记录，保留 4 条规范数据源。
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              合并时受影响的
              <span className="font-mono text-gray-300"> property_measurements </span>
              行的 <span className="font-mono text-gray-300">source_id</span>
              被置为 NULL——数值本身未变，但原始来源引用不可再追溯。
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              10 个仅关联占位数据源的数据集在清理中被级联删除，随后由迁移 073
              按原占位标题逐字恢复（页面上如实显示占位来源标题）。
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">对使用者意味着什么</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              物性数值与单位不受影响；仅来源引用缺失的行会在表格中显示
              “来源信息缺失”提示。
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              点击该提示可查看完整说明；每条受影响的测量行均可独立关闭提示。
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">完整事后分析</h2>
          <p className="text-gray-400 leading-relaxed">
            完整的事后分析（含迁移脚本、影响面清单与恢复方案）由工程团队维护，
            本页为其摘要。如有疑问，请通过项目仓库的 issue 渠道联系维护者。
          </p>
        </section>
      </main>
    </div>
  )
}
