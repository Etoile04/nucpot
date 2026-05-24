import { describe, it, expect } from 'vitest'
import { GET as getPotentials } from '@/app/api/potentials/route'
import { NextRequest } from 'next/server'

describe('Seed Data Integrity', () => {
  it('covers all nuclear material systems', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=50'))
    const res = await getPotentials(req)
    const data = await res.json()

    const names = data.potentials.map((p: { name: string }) => p.name)
    // U-Zr
    expect(names.some((n: string) => n.includes('UZr') || n.includes('U-Zr'))).toBe(true)
    // U system
    expect(names.some((n: string) => n.includes('U_') || n.includes('UMo') || n.includes('UNb') || n.includes('UPu') || n.includes('UO') || n.includes('USi'))).toBe(true)
    // Zr
    expect(names.some((n: string) => n.includes('Zr'))).toBe(true)
    // Fe
    expect(names.some((n: string) => n.includes('Fe'))).toBe(true)
  })

  it('includes multiple potential types', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=50'))
    const res = await getPotentials(req)
    const data = await res.json()

    const types = [...new Set(data.potentials.map((p: { type: string }) => p.type))]
    expect(types).toContain('EAM')
    expect(types).toContain('MEAM')
    expect(types.length).toBeGreaterThanOrEqual(4) // EAM, MEAM, Buckingham, other, etc.
  })

  it('each potential has required fields', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=50'))
    const res = await getPotentials(req)
    const data = await res.json()

    data.potentials.forEach((p: { name: string; type: string; elements: string[]; references: unknown[]; tags: string[] }) => {
      expect(p.name).toBeTruthy()
      expect(p.type).toBeTruthy()
      expect(p.elements).toBeInstanceOf(Array)
      expect(p.elements.length).toBeGreaterThan(0)
      expect(p.references).toBeInstanceOf(Array)
      expect(p.tags).toBeInstanceOf(Array)
    })
  })

  it('U-Zr potential has correct metadata', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?elements=U,Zr'))
    const res = await getPotentials(req)
    const data = await res.json()

    expect(data.potentials.length).toBeGreaterThan(0)
    const uzr = data.potentials.find((p: { name: string }) => p.name.includes('Moore') || p.name.includes('UZr'))
    expect(uzr).toBeDefined()
    expect(uzr!.type).toBeTruthy()
    expect(uzr!.elements).toContain('U')
    expect(uzr!.elements).toContain('Zr')
  })

  it('search by type returns correct results', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?type=MEAM'))
    const res = await getPotentials(req)
    const data = await res.json()

    expect(data.total).toBeGreaterThan(0)
    data.potentials.forEach((p: { type: string }) => {
      expect(p.type).toBe('MEAM')
    })
  })
})
