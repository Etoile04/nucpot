import { describe, it, expect } from 'vitest'
import { GET } from '@/app/api/stats/route'

describe('GET /api/stats', () => {
  it('returns stats with correct structure', async () => {
    const res = await GET()
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.totalPotentials).toBe(50)
    expect(data.types).toContain('EAM')
    expect(data.types).toContain('MEAM')
    expect(data.types.length).toBeGreaterThanOrEqual(4)
    expect(data.elements).toContain('U')
    expect(data.elements).toContain('Zr')
    expect(data.elements).toContain('Fe')
    expect(data.recent).toBeInstanceOf(Array)
  })
})
