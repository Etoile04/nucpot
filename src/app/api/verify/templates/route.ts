import { NextRequest, NextResponse } from 'next/server'

const AUTOCV_API_URL = process.env.NEXT_PUBLIC_AUTOCV_API_URL || 'http://localhost:8000'

export async function GET(request: NextRequest) {
  const url = `${AUTOCV_API_URL}/api/templates`
  try {
    const upstream = await fetch(url)
    const body = await upstream.text()
    return new NextResponse(body, {
      status: upstream.status,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (error) {
    return NextResponse.json({ error: 'Verification service unavailable' }, { status: 502 })
  }
}
