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
              覆盖金属燃料（U-Zr、U-Mo）、氧化物燃料（UO₂）、包壳材料（Zr、Zr-Nb）、结构材料（Fe）
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              支持经典势（EAM、MEAM）和机器学习势（RANN）
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              元数据参考 OpenKIM EDN 标准
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              与主流模拟软件（LAMMPS、GULP）兼容
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

        {/* Section 3: 协作团队 */}
        <section>
          <h2 className="text-2xl font-semibold mb-4">协作团队</h2>
          <ul className="space-y-2 text-gray-400">
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              湖南大学邓辉球团队 — 势函数梳理与设计
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              核动力院 — 核心协作方
            </li>
            <li className="flex items-start gap-2">
              <span className="text-blue-400 mt-1">•</span>
              李文杰 — 项目推进与标准制定
            </li>
          </ul>
        </section>

        <hr className="border-gray-700" />

        {/* Section 4: 联系方式 */}
        <section>
          <h2 className="text-2xl font-semibold mb-4">联系方式</h2>
          <p className="text-gray-300 mb-3">如有问题或合作意向，请通过以下方式联系：</p>
          <p className="text-gray-400">
            📧&nbsp;
            <a href="mailto:nucpot@example.com" className="text-blue-400 hover:underline">
              nucpot@example.com
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
