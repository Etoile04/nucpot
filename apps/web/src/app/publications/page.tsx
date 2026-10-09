import type { Metadata } from "next"
import LiteratureManager from "./LiteratureManager"

export const metadata: Metadata = {
  title: "文献库 - NucPot",
  description: "管理核材料文献库：上传 PDF、检索文献、追踪提取状态、并触发 LLM 提取。",
}

// NFM-5418: without this, a fully-static page emits Next.js's default
// s-maxage=31536000 HTML header, so the Cloudflare edge pins it for a year
// and any deploy that changes chunk hashes orphans the cached refs
// (NFM-5335 class). Match the home page's ISR window instead: the shell
// revalidates every 5 min behind stale-while-revalidate.
export const revalidate = 300

/**
 * /literature — Literature Management (Pipeline A: Extraction)
 *
 * Provides the user-facing entry point for the V1 extraction pipeline:
 *   POST /api/v1/literature/upload          — Upload a PDF (multipart)
 *   POST /api/v1/literature/from-doi        — Fetch paper by DOI
 *   GET  /api/v1/literature                 — Paginated list (filters)
 *   GET  /api/v1/literature/search?q=       — Full-text search
 *   GET  /api/v1/literature/{id}            — Full detail + extraction results
 *   GET  /api/v1/literature/{id}/status     — Processing status
 *   POST /api/v1/literature/{id}/reextract  — Trigger re-extraction
 *   DELETE /api/v1/literature/{id}          — Delete + associated data
 *
 * Previously this route was missing from the Next.js app/ tree while the
 * /literature Nav entry shipped — clicking the nav link produced a 404.
 * The fix routes the existing V1 API into a 3-pane UI (list / search /
 * upload + detail drawer) so the Nav, the API, and the page agree.
 */
export default function LiteraturePage() {
  return <LiteratureManager />
}
