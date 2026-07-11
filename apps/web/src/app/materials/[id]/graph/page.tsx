import type { Metadata } from "next"
import { MaterialGraphView } from "./MaterialGraphView"

export const metadata: Metadata = {
  title: "知识图谱 - NFMD",
  description: "查看材料知识图谱邻域子图",
}

interface PageProps {
  readonly params: Promise<{ id: string }>
}

export default async function MaterialGraphPage({
  params,
}: PageProps) {
  const { id } = await params
  return <MaterialGraphView materialId={id} />
}
