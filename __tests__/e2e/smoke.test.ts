import { describe, it, expect } from 'vitest'
import { GET as getPotentials } from '@/app/api/potentials/route'
import { GET as getStats } from '@/app/api/stats/route'
import { NextRequest } from 'next/server'

describe('E2E Smoke Tests', () => {
  it('full pipeline: stats API returns correct data', async () => {
    const res = await getStats()
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.totalPotentials).toBe(10)
    expect(data.types.length).toBeGreaterThanOrEqual(4) // EAM, MEAM, ML, Buckingham, other
    expect(data.elements).toContain('U')
    expect(data.elements).toContain('Zr')
    expect(data.elements).toContain('Fe')
  })

  it('full pipeline: browse returns all 10 potentials', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=20'))
    const res = await getPotentials(req)
    const data = await res.json()

    expect(data.total).toBe(10)
    expect(data.potentials).toHaveLength(10)
  })
})
