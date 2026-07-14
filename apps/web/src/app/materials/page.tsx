import type { Metadata } from "next"
import { MaterialsListView } from "./MaterialsListView"

export const metadata: Metadata = {
  title: "材料列表 - NFMD",
  description: "浏览核燃料与材料数据库中的全部材料",
}

export default function MaterialsListPage() {
  return <MaterialsListView />
}
