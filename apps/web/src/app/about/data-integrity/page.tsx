/**
 * /about/data-integrity — 数据完整性披露（最终版 / CTO-owned, NFM-4249）。
 *
 * Spec NFM-4134.A §8 #4 指定的 `<DataLossNotice>` "了解详情" 链接的目的地。
 * 内容为 CTO 拥有；本版本为 NFM-4249 最终文案，作为 PR #1133 stub 的
 * 后续 PR 落地（见 ADR-012）。
 *
 * 风格与现有 /about 页面保持一致（深色底、zh-CN 文案）。
 *
 * Layout invariant (NFM-4252): each bullet `<li>` is a flex row and MUST hold
 * exactly two flex children — the `•` marker and a single wrapper `<span
 * class="min-w-0">` carrying all of the sentence. Text placed directly in the
 * flex row becomes an *anonymous flex item* per run, so an inline `<span
 * class="font-mono">` splits one sentence into several items that share one
 * nowrap flex line and each shrink to min-content. CJK min-content is one
 * character, so at 375 px the prose collapses into 1-character-wide columns
 * (measured on the pre-fix tree: 330 px tall vs 66 px fixed) and the tail of
 * the sentence is pushed past the viewport, where `body { overflow: hidden }`
 * in layout.tsx *clips* it — silently truncating disclosure copy rather than
 * producing a scrollbar. ADR-012 §3's markup landed with this vulnerable
 * pattern (PR #1136); this file re-lands that copy character-for-character
 * inside the fixed structure, per the CPO reconciliation ruling on NFM-4252.
 *
 * Keep the wrapper. Keep `whitespace-nowrap` on the mono runs so identifiers
 * break between tokens rather than inside them; note that the mono runs are
 * never themselves split by this bug, so `break-words` on them is not a fix
 * and would re-admit mid-identifier breaks.
 */
export default function DataIntegrityPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12 space-y-12">
        <section>
          <h1 className="text-3xl font-bold mb-4">数据完整性说明</h1>
          <p className="text-gray-300 text-lg leading-relaxed mb-6">
            本页说明 2026-09-02 数据库迁移对部分物性数据来源标注的影响，
            以及我们已经采取的恢复措施。我们以脚注的方式在受影响的测量行上作出披露，
            而不是隐藏这一限制。
          </p>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">发生了什么</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                2026-09-02，迁移 070（提交{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  444469cda
                </span>
                ） 执行时把同一文献的重复占位{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  data_sources
                </span>
                合并到 4 条规范记录上；合并过程中级联删除了 10
                个仅关联占位数据源的数据集， 以及 31 条{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  property_measurements
                </span>
                测量记录（涉及 UO₂、U-10Mo 等真实材料的全部在库测量行）。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                事故根因：迁移 070 的“坏数据源”判定范围过宽，把{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  Unknown Source
                </span>{" "}
                与{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  Unattributed source (no DOI)
                </span>{" "}
                等占位标题下的 18 条真实来源（无
                DOI、file_hash、content_md）误判为去重候选。
                完整的根因分析与影响面清单见
                <a
                  href="/NFM/issues/NFM-4130"
                  className="text-blue-400 hover:underline"
                >
                  NFM-4130
                </a>
                。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">恢复措施</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  NFM-4130
                </span>
                （提交{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  570a2e2f
                </span>
                ，PR #1107）——将迁移 070 的“坏数据源”判定范围收窄至 UUID
                标题行，移除占位标题类，防止占位合并再次触发。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                迁移{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  075
                </span>
                （NFM-4139）——从{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  data_sources_backup_070
                </span>{" "}
                与{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  datasets_backup_070
                </span>{" "}
                备份表， 按原占位标题逐字恢复了 18 条{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  data_sources
                </span>
                与 10 条{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  datasets
                </span>{" "}
                记录。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                迁移{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  079
                </span>
                （NFM-4191）——从{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  property_measurements_backup_070
                </span>{" "}
                备份表，恢复了被级联删除的 31 条测量记录。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                迁移{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  077
                </span>
                （NFM-4159 §5.1）——将{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  datasets.source_id
                </span>{" "}
                设为可空，使 API 能以{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  WHERE source_id IS NULL
                </span>{" "}
                过滤出仍缺失来源引用的测量行。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">对使用者意味着什么</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                物性数值与单位在迁移前后一致；物性查询返回的数据未因本次事故发生单位或数量级变化。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                仅来源引用缺失的测量行，会在属性表格中显示“来源信息缺失”提示，
                并可逐条关闭（关闭状态保存在浏览器本地）。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                完整数据集（dataset）的占位来源标题以{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  Unattributed source (no DOI)
                </span>{" "}
                如实显示，未作美化包装。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                我们正在尝试重新识别可恢复的来源引用；定位到原始文献后，
                对应行的“来源信息缺失”提示将转为正常引用。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">完整事后分析</h2>
          <p className="text-gray-400 leading-relaxed mb-3">
            完整的事后分析（含迁移脚本、影响面清单与恢复方案）由工程团队维护，
            详见
            <a
              href="/NFM/issues/NFM-4130"
              className="text-blue-400 hover:underline"
            >
              NFM-4130
            </a>
            。
          </p>
          <p className="text-gray-400 leading-relaxed">
            如对具体行有疑问，或可补充某一行的来源信息，请通过项目仓库的 issue
            渠道联系维护者。
          </p>
        </section>
      </main>
    </div>
  );
}
