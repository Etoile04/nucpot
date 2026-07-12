import type { Metadata } from "next"
import { MaterialPropertiesView } from "./MaterialPropertiesView"

export const metadata: Metadata = {
  title: "材料属性 - NFMD",
  description: "查看材料物理属性与置信度评估",
}

interface PageProps {
  readonly params: Promise<{ id: string }>
}

export default async function MaterialPropertiesPage({
  params,
}: PageProps) {
  const { id } = await params
  return <MaterialPropertiesView materialId={id} />
}
