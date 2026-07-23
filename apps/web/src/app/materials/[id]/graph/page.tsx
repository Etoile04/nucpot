import type { Metadata } from "next"
import { BreadcrumbNav, type BreadcrumbItem } from "@/components/BreadcrumbNav"
import { MaterialSubgraphView } from "@/components/materials/MaterialSubgraphView"

export const metadata: Metadata = {
  title: "材料知识图谱 - NFMD",
  description: "查看与该材料关联的属性、实验、条件与相邻材料的知识图谱",
}

const BREADCRUMB_ITEMS: readonly BreadcrumbItem[] = [
  { label: '首页', href: '/' },
  { label: '材料库', href: '/materials' },
  { label: '材料图谱', href: '' },
]

interface PageProps {
  readonly params: Promise<{ id: string }>
}

export default async function MaterialGraphPage({ params }: PageProps) {
  const { id } = await params
  return (
    <>
      <BreadcrumbNav items={BREADCRUMB_ITEMS} />
      <MaterialSubgraphView materialId={id} />
    </>
  )
}
