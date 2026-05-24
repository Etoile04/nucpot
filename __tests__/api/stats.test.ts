import { describe, it, expect } from 'vitest'
import { GET } from '@/app/api/stats/route'

describe('GET /api/stats', () => {
  it('returns stats with correct structure', async () => {
    const res = await GET()
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.totalPotentials).toBe(10)
    expect(data.types).toContain('EAM')
    expect(data.types).toContain('MEAM')
    expect(data.types).toContain('ML')
    expect(data.elements).toContain('U')
    expect(data.elements).toContain('Zr')
    expect(data.elements).toContain('Fe')
    expect(data.recent).toBeInstanceOf(Array)
  })
})
