import type { Metadata } from "next"
import LiteratureDetailContent from "./LiteratureDetailContent"

export const metadata: Metadata = {
  title: "文献详情 - NucPot",
  description: "查看文献元数据、提取结果和全文内容。",
}

interface LiteratureDetailPageProps {
  readonly params: Promise<{ readonly id: string }>
}

/**
 * /literature/[id] — Literature Detail Page
 *
 * Renders a full-page view of a single literature item, including:
 *   • Metadata (title, DOI, journal, year, status)
 *   • Abstract
 *   • Extracted full-text markdown
 *   • Figures
 *   • Extraction results organized by provenance section
 *   • Actions (re-extract, delete)
 *   • Back navigation to /literature list
 *
 * The API GET /api/v1/literature/{id} already works; this route
 * provides the missing frontend page that the list's title links
 * (<a href="/literature/{id}">) point to.
 */
export default async function LiteratureDetailPage({
  params,
}: LiteratureDetailPageProps) {
  const { id } = await params
  return <LiteratureDetailContent literatureId={id} />
}
