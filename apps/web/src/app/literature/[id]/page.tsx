import type { Metadata } from "next"
import { notFound } from "next/navigation"
import LiteratureDetailView from "./LiteratureDetailView"
import LiteratureGraphView from "./LiteratureGraphView"

interface LiteratureDetailPageProps {
  readonly params: Promise<{ id: string }>
  readonly searchParams: Promise<{ view?: string | string[] }>
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
 * Default view is **Layout B** per docs/specs/G1-extraction-value-
 * presentation.md §4.1 + AC-3 (GraphCanvas + 属性侧栏 + 校对抽屉).
 * The legacy flat extraction-results view is preserved behind
 * `?view=flat` for the screenshot regression baseline + admin actions
 * (re-extract / delete) that have no Layout B home yet.
 *
 * The page is fully client-side rendered (no SSR data fetch) because the
 * API runs on a separate origin that may not be reachable during build.
 */
export default async function LiteratureDetailPage({
  params,
  searchParams,
}: LiteratureDetailPageProps) {
  const { id } = await params
  const sp = await searchParams
  const view = Array.isArray(sp.view) ? sp.view[0] : sp.view

  if (!id || id.length < 32) {
    notFound()
  }

  if (view === "flat") {
    return <LiteratureDetailView literatureId={id} />
  }

  return <LiteratureGraphView literatureId={id} />
}
