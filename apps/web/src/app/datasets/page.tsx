import type { Metadata } from "next"
import { DatasetsListView } from "./DatasetsListView"

export const metadata: Metadata = {
  title: "数据集 - NucPot",
  description:
    "核材料数据集列表：按材料与数据源分组的实验与计算测量集合，支持分页浏览。",
}

// NFM-5321 (D1/D4, Visual-Truth Gate retro-gate): the hero copy previously
// leaked internal ticket ids and a contributor's local file path; the page
// also carried its own dark gradient wrapper, which none of the sibling
// list destinations (/materials, /potentials) use — the shared site chrome
// already provides the dark surface via antd's dark algorithm. The view
// now owns the standard `max-w-[1200px]` container like MaterialsListView.
export default function DatasetsPage() {
  return <DatasetsListView />
}
