import { NextResponse } from 'next/server'

const API_BASE = process.env.API_SERVER_URL || 'http://nucpot-prod-api:8000'

export async function GET() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/stats`, { headers: { 'Content-Type': 'application/json' } })
    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ totalPotentials: 0, totalTypes: 0, totalElements: 0, types: [], elements: [], recent: [] }, { status: 200 })
  }
}
