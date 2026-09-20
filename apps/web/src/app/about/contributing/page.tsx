/**
 * /about/contributing — 贡献指南（NFM-4991 P2）。
 *
 * 内容面向两种贡献者：势函数 / 数据集贡献者（通过 /upload 流程），
 * 与代码贡献者（通过 fork + PR 流程）。本页面只是项目根目录
 * CONTRIBUTING.md 的镜像， 保留 GitHub 兼容入口。
 *
 * Style invariant (NFM-4252): bullet `<li>` rows MUST hold exactly two
 * flex children — the `•` marker and a single wrapper `<span class="min-w-0">`
 * carrying all of the sentence. See /about/data-integrity/page.tsx.
 */
export default function ContributingPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="max-w-4xl mx-auto px-6 py-12 space-y-12">
        <section>
          <h1 className="text-3xl font-bold mb-4">贡献指南</h1>
          <p className="text-gray-300 text-lg leading-relaxed mb-6">
            NucPot 接受两类贡献：势函数 / 数据集的内容贡献（通过站内{" "}
            <a href="/upload" className="text-blue-400 hover:underline">
              上传势函数
            </a>{" "}
            流程），以及代码 / 文档的工程贡献（通过 GitHub fork + PR）。
            本页为站内入口；完整规则与 CI 强制项请参阅仓库根目录{" "}
            <a
              href="https://github.com/Etoile04/nucpot/blob/master/CONTRIBUTING.md"
              className="text-blue-400 hover:underline"
              rel="noopener noreferrer"
              target="_blank"
            >
              CONTRIBUTING.md
            </a>
            。
          </p>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">提交势函数 / 数据集</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                登录后进入{" "}
                <a href="/upload" className="text-blue-400 hover:underline">
                  上传势函数
                </a>{" "}
                页面填写名称、模型类别、参数文件；文件以{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  .eam.alloy
                </span>{" "}
                /{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  .meam
                </span>{" "}
                /{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  .library
                </span>{" "}
                /{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  .nnp
                </span>{" "}
                为主， 也接受 ZIP 打包。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                数据集须填写材料、数据源（DOI 优先， 无 DOI
                须注明出处）与测量日期； 字段缺失会被审稿退回。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                上传后系统自动入库{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  review_queue
                </span>{" "}
                并通知审核员； 通过后势函数出现在{" "}
                <a href="/potentials" className="text-blue-400 hover:underline">
                  势函数列表
                </a>{" "}
                ，数据集出现在{" "}
                <a href="/datasets" className="text-blue-400 hover:underline">
                  数据集
                </a>{" "}
                。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                所有贡献默认遵循{" "}
                <a
                  href="https://github.com/Etoile04/nucpot/blob/master/LICENSE"
                  className="text-blue-400 hover:underline"
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  MIT License
                </a>
                ； 引用既有文献请保留原始 DOI。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">提交代码 / 文档</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                Fork 仓库后基于{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  master
                </span>{" "}
                新建分支，分支名形如{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  NFM-4991-phase2-datasets
                </span>{" "}
                。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                每个 commit 必须包含{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  NFM-###
                </span>{" "}
                或显式{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  [no-issue]
                </span>{" "}
                标记； CI 会拒绝未带标记的 PR 与直接 push。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                PR 通过前需保证：lint / typecheck / 测试通过；
                前端变更需在 PR 描述中附视觉回归截图（关键断点 320 / 768 / 1024 / 1440）。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                后端 API 变更需同步更新 OpenAPI 描述（FastAPI 自动生成
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  {" "}
                  /openapi.json
                </span>
                ）； 破坏性变更必须升 v2。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">协作团队</h2>
          <p className="text-gray-300 leading-relaxed mb-3">
            当前协作团队与各自的归属领域如下，欢迎通过{" "}
            <a
              href="/feedback"
              className="text-blue-400 hover:underline"
            >
              反馈
            </a>{" "}
            渠道联系：
          </p>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                湖南大学邓辉球团队 — 势函数梳理与设计、U-Mo / U-Zr 等金属燃料评估。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                核动力院 — 需求对接、应用场景与基准核数据校对。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                李文杰 — 项目开发与维护、 数据库 / API / 前端工程实现。
              </span>
            </li>
          </ul>
          <p className="text-gray-400 leading-relaxed mt-3">
            项目立项过程中得到中核集团焦拥军首席专家提议建设开源势函数网站。
          </p>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">审稿与争议解决</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                上传的势函数 / 数据集由审核队列（{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  review_queue
                </span>
                ）异步评审； 普通用户可在{" "}
                <a
                  href="/review/kg"
                  className="text-blue-400 hover:underline"
                >
                  审核队列
                </a>{" "}
                查看进度。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                数据来源冲突（同一物性多份测量值差距较大）走{" "}
                <a
                  href="/review/conflicts"
                  className="text-blue-400 hover:underline"
                >
                  冲突审核
                </a>{" "}
                流程， 决策留痕在{" "}
                <span className="font-mono text-gray-300 whitespace-nowrap">
                  review_decisions
                </span>{" "}
                表。
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              <span className="min-w-0">
                涉及合规披露的争议（NFM-4159 §5.2）由 CPO 仲裁，
                通过 ADR 渠道增订 / 修订。
              </span>
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        <section>
          <h2 className="text-2xl font-semibold mb-4">联系方式</h2>
          <p className="text-gray-300 leading-relaxed">
            技术问题与合规争议请通过站内{" "}
            <a href="/feedback" className="text-blue-400 hover:underline">
              反馈
            </a>{" "}
            渠道提交；商务合作可直接邮件{" "}
            <a
              href="mailto:liwenjie@npic.ac.cn"
              className="text-blue-400 hover:underline"
            >
              liwenjie@npic.ac.cn
            </a>
            。
          </p>
        </section>
      </main>
    </div>
  );
}