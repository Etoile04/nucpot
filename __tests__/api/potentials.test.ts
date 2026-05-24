import { describe, it, expect } from 'vitest'
import { GET } from '@/app/api/potentials/route'
import { NextRequest } from 'next/server'

describe('GET /api/potentials', () => {
  it('returns potentials list with default pagination', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.potentials).toBeInstanceOf(Array)
    expect(data.total).toBe(50)
    expect(data.page).toBe(1)
    expect(data.limit).toBe(20)
  })

  it('filters by type=EAM', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?type=EAM'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.total).toBeGreaterThan(0)
    data.potentials.forEach((p: { type: string }) => {
      expect(p.type).toBe('EAM')
    })
  })

  it('filters by elements U,Zr (overlaps: returns records containing U or Zr)', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?elements=U,Zr'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.total).toBeGreaterThan(0)
    // overlaps filter: each returned potential must contain at least one of U or Zr
    data.potentials.forEach((p: { elements: string[] }) => {
      const hasU = p.elements.includes('U')
      const hasZr = p.elements.includes('Zr')
      expect(hasU || hasZr).toBe(true)
    })
  })

  it('returns empty for nonexistent type', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?type=NONEXISTENT'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.potentials).toHaveLength(0)
    expect(data.total).toBe(0)
  })

  it('respects pagination limit', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=3'))
    const res = await GET(req)
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.potentials).toHaveLength(3)
    expect(data.total).toBe(50)
    expect(data.totalPages).toBe(17)
  })
})
