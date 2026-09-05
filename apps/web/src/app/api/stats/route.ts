import { NextResponse } from "next/server"

const API_BASE = process.env.API_SERVER_URL || "http://nucpot-prod-api:8000"

export async function GET() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/stats`, {
      headers: { "Content-Type": "application/json" },
    })
    const envelope = await res.json()
    // Unwrap the standard FastAPI ApiResponse envelope ({success, data, error})
    // so browser consumers in apps/web/src/app/browse/BrowseView.tsx and
    // apps/web/src/app/search/SearchView.tsx can read `data.elements`
    // directly. Without this, `[...data.elements]` spreads `undefined` and
    // the element-filter chips silently disappear (NFM-4341).
    return NextResponse.json(envelope.data ?? {})
  } catch {
    return NextResponse.json(
      { totalPotentials: 0, totalTypes: 0, totalElements: 0, types: [], elements: [], recent: [] },
      { status: 200 },
    )
  }
}
