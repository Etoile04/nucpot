import type { Metadata } from "next"
import { MaterialDetailContent } from "./MaterialDetailContent"

export const metadata: Metadata = {
  title: "材料详情 - NFMD",
  description: "查看材料详细信息，包括化学式、晶体结构、别名与组成",
}

interface PageProps {
  readonly params: Promise<{ id: string }>
}

export default async function MaterialDetailPage({ params }: PageProps) {
  const { id } = await params
  return <MaterialDetailContent materialId={id} />
}
