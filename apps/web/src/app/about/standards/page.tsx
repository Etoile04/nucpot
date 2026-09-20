/**
 * /about/standards — 数据 / 元数据 / 标识符 规范说明（NFM-4991 P2）。
 *
 * 用于替代 /about 页内简短的标准说明（旧版只是一句"元数据参考
 * OpenKIM EDN 标准"），并把 §5.2 attribution 锁约定、DOI/UUID
 * 命名规范、版本控制策略等一并写齐。PR-backlog candidate: 把
 * "数据来源" 段从 /about 主页迁过来，作为 standards 的子节，
 * 让 /about 主页只剩"项目背景"和"协作团队"。
 *
 * Style invariant (NFM-4252): bullet `<li>` rows MUST hold exactly two
 * flex children — the `•` marker and a single wrapper `<span class="min-w-0">`
 * carrying all of the sentence. Anonymous flex items at 375 px viewport
 * collapse CJK prose to 1-character-wide columns and clip it via
 * `body { overflow: hidden }`. See /about/data-integrity/page.tsx for the
 * full rationale.
 */
export default function StandardsPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12 space-y-12">
        <section>
          <h1 className="text-3xl font-bold mb-4">数据与元数据规范</h1>
          <p className="text-gray-300 text-lg leading-relaxed mb-6">
            本页汇总 NucPot 采用的数据、元数据与标识符规范。每一条规范都对应一个
            既定的外部标准或一个内部 ADR， 便于贡献者核对实现是否符合项目基线。
          </p>
          <p className="text-gray-400 text-sm leading-relaxed">
            详细内容按主题分组：势函数格式参考{" "}
            <a
              href="https://openkim.org/"
              className="text-blue-400 hover:underline"
              rel="noopener noreferrer"
              target="_blank"
            >
              OpenKIM
            </a>
            ，数据迁移遵循修订章节
            <a
              href="/NFM/issues/NFM-4130"
              className="text-blue-400 hover:underline"
            >
              NFM-4130
            </a>
            ，合规披露遵循{" "}
            <span className="font-mono text-gray-300 whitespace-nowrap">
              §5.2 attribution
            </span>{" "}
            锁定合同（NFM-4159）。
          </p>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">势函数元数据</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                势函数描述遵循{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  OpenKIM EDN
                </span>{" "}
                格式：每个势函数条目给出 species、model、citations
                三个核心字段，元素符号使用 IUPAC 大小写规范。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                模型分类支持经典势（EAM、MEAM、EAM-ADP）与机器学习势
                （RANN、ACE、NEP）；新模型在{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  Model.kind
                </span>{" "}
                字段落地前需先在 issue 通道评审。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                与{" "}
                <a
                  href="https://www.ctcms.nist.gov/potentials/"
                  className="text-blue-400 hover:underline"
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  NIST Interatomic Potentials Repository (IPR)
                </a>{" "}
                对齐：相同的势函数在 IPR 中存在记录时，NucPot 链接优先指向 IPR 条目以减少重复维护。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">标识符与命名</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                所有内部主键（势函数、材料、文献、数据集、测量）使用
                UUID v4， 在 API 路径中通过{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  /api/v1/{`<resource>`}/{`<uuid>`}
                </span>{" "}
                暴露。 FastAPI 自动校验 UUID 格式，非法值返回 422。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                文献以 DOI 为首选标识符；无 DOI 的来源退化为{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  source.file_hash
                </span>{" "}
                +{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  content_md
                </span>{" "}
                指纹， 保证数据源消重的唯一性。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                项目根目录下{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  CONTRIBUTING.md
                </span>{" "}
                列出 commit 引用规则（NFM-2081 / NFM-2204）；所有
                PR 与直接 push 的提交主题必须包含{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  NFM-###
                </span>{" "}
                或显式的{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  [no-issue]
                </span>{" "}
                标记。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">合规披露（NFM-4159 §5.2）</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                单条数据集 / 势函数的响应包含{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  attribution.status
                </span>{" "}
                ∈ {"{"}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  placeholder
                </span>
                ,{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  intact
                </span>
                {"}"}；该字段为 LOCKED 合同， 不允许新增第三状态。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  placeholder
                </span>{" "}
                状态在 10 条 recast-restored 数据集上命中（见 NFM-4136 / NFM-4159
                §5.2）， 用于披露 2026-09-02 迁移 070 后的来源引用状态；
                标题字段本身即披露渠道，前端只渲染提示横幅。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                完整披露文案（迁移影响、恢复措施、使用者须知）见{" "}
                <a
                  href="/about/data-integrity"
                  className="text-blue-400 hover:underline"
                >
                  数据完整性说明
                </a>
                。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">版本与发布</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                API 路径按 semver 组织：当前在{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  /api/v1
                </span>{" "}
                下提供， 任何破坏性变更必须升到{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  /api/v2
                </span>{" "}
                而非就地变更 v1。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                数据库迁移通过 Alembic 编号化（{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  070
                </span>
                、{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  075
                </span>{" "}
                等）； 每个迁移在 PR 描述中标注对应 NFM 工单与影响面。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                部署通过 ADR-018 直连通道（dev/hotpublic → prod，
                详见 NFM-4762），PR-merged + docker-build
                成功后自动滚动部署到生产。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">API 与下游兼容</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                与{" "}
                <a
                  href="https://www.lammps.org/"
                  className="text-blue-400 hover:underline"
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  LAMMPS
                </a>
                、{" "}
                <a
                  href="https://gulp.curtin.edu.au/"
                  className="text-blue-400 hover:underline"
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  GULP
                </a>{" "}
                兼容：势函数{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  parameters
                </span>{" "}
                字段保留两套导出选项（LAMMPS{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  .eam.alloy
                </span>{" "}
                / GULP{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  .library
                </span>
                ）。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                FastAPI 自动挂载{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  /docs
                </span>{" "}
                Swagger UI 与{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  /openapi.json
                </span>{" "}
                描述；前端{" "}
                <a
                  href="/api-docs"
                  className="text-blue-400 hover:underline"
                >
                  API 文档
                </a>{" "}
                页 iframe 上述 Swagger， 详情见 NFM-4991。
              </span>
            </li>
          </ul>
        </section>
      </main>
    </div>
  );
}