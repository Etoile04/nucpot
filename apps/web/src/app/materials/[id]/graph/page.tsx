import type { Metadata } from "next"
import { MaterialSubgraphView } from "@/components/materials/MaterialSubgraphView"

export const metadata: Metadata = {
  title: "材料知识图谱 - NFMD",
  description: "查看与该材料关联的属性、实验、条件与相邻材料的知识图谱",
}

interface PageProps {
  readonly params: Promise<{ id: string }>
}

export default async function MaterialGraphPage({ params }: PageProps) {
  const { id } = await params
  return <MaterialSubgraphView materialId={id} />
}