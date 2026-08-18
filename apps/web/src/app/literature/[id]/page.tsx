import type { Metadata } from "next"
import { notFound } from "next/navigation"
import LiteratureDetailView from "./LiteratureDetailView"

interface LiteratureDetailPageProps {
  readonly params: Promise<{ id: string }>
}

export async function generateMetadata({
  params,
}: LiteratureDetailPageProps): Promise<Metadata> {
  const { id } = await params

  return {
    title: `文献详情 - NucPot`,
    description: `查看文献 ${id} 的详细信息和提取结果。`,
  }
}

/**
 * /literature/{uuid} — Literature Detail (deep-link page)
 *
 * Serves as the canonical deep-link target for literature items. The list
 * page at /literature renders an <a href="/literature/{id}"> on each row
 * title so that right-click / open-in-new-tab lands here instead of 404.
 *
 * The page is fully client-side rendered (no SSR data fetch) because the
 * API runs on a separate origin that may not be reachable during build.
 */
export default async function LiteratureDetailPage({
  params,
}: LiteratureDetailPageProps) {
  const { id } = await params

  if (!id || id.length < 32) {
    notFound()
  }

  return <LiteratureDetailView literatureId={id} />
}
