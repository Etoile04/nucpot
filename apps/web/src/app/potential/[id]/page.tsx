import type { Metadata } from "next"
import { PotentialDetailPage } from "@/components/potential/PotentialDetailPage"

export const metadata: Metadata = {
  title: "势函数详情 - NFMD",
  description: "核材料势函数详情页",
}

interface PotentialPageProps {
  params: Promise<{ id: string }>
}

export default async function PotentialPage({ params }: PotentialPageProps) {
  const { id } = await params
  return <PotentialDetailPage id={id} />
}
