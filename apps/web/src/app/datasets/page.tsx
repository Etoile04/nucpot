import type { Metadata } from "next"
import { DatasetsListView } from "./DatasetsListView"

export const metadata: Metadata = {
  title: "数据集 - NucPot",
  description:
    "核材料数据集列表：按材料 + 数据源分组的实验与计算测量集合。",
}

export default function DatasetsPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="mx-auto max-w-6xl px-6 py-10 space-y-6">
        <header className="space-y-1">
          <h1 className="text-3xl font-bold">数据集</h1>
          <p className="text-gray-300 text-sm leading-relaxed">
            核材料数据集：每个数据集是来自一个材料 + 一个数据源（文献 / 实验报告 / 数据库）的一组测量。
            NFM-4991 启用项。查看目标站点地图:{" "}
            <code className="px-1 py-0.5 rounded bg-gray-800 text-blue-300">
              ~/.openclaw/workspace-researcher/docs/nuclear-potentials-platform/platform-design.md
            </code>{" "}
            §第二部分第 1 节。
          </p>
        </header>
        <DatasetsListView />
      </main>
    </div>
  )
}