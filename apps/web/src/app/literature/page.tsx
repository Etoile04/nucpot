import type { Metadata } from "next"
import LiteratureManager from "./LiteratureManager"

export const metadata: Metadata = {
  title: "文献管理 - NucPot",
  description:
    "管理核材料文献库：上传 PDF、检索文献、追踪提取状态、并触发 LLM 提取。",
}

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