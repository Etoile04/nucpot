import { describe, it, expect } from 'vitest'
import { GET as getPotentials } from '@/app/api/potentials/route'
import { NextRequest } from 'next/server'

describe('Seed Data Integrity', () => {
  it('covers all nuclear material systems', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=20'))
    const res = await getPotentials(req)
    const data = await res.json()

    const names = data.potentials.map((p: { name: string }) => p.name)
    // U-Zr
    expect(names.some((n: string) => n.includes('UZr') || n.includes('U-Zr'))).toBe(true)
    // Pure U
    expect(names.some((n: string) => n.includes('U_Fernandez') || n.includes('U_') && !n.includes('UO'))).toBe(true)
    // UO2
    expect(names.some((n: string) => n.includes('UO2') || n.includes('UO₂'))).toBe(true)
    // Zr
    expect(names.some((n: string) => n.includes('Zr'))).toBe(true)
    // Fe
    expect(names.some((n: string) => n.includes('Fe'))).toBe(true)
  })

  it('includes multiple potential types', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=20'))
    const res = await getPotentials(req)
    const data = await res.json()

    const types = [...new Set(data.potentials.map((p: { type: string }) => p.type))]
    expect(types).toContain('EAM')
    expect(types).toContain('MEAM')
    expect(types).toContain('ML')
  })

  it('each potential has required fields', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?limit=20'))
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
    const uzr = data.potentials.find((p: { name: string }) => p.name.includes('Moore'))
    expect(uzr).toBeDefined()
    expect(uzr.type).toBe('MEAM')
    expect(uzr.elements).toContain('U')
    expect(uzr.elements).toContain('Zr')
    expect(uzr.references).toBeInstanceOf(Array)
    expect(uzr.references.length).toBeGreaterThan(0)
  })

  it('search by type returns correct results', async () => {
    const req = new NextRequest(new URL('http://localhost:3000/api/potentials?type=ML'))
    const res = await getPotentials(req)
    const data = await res.json()

    expect(data.total).toBe(1)
    expect(data.potentials[0].name).toContain('Zr')
    expect(data.potentials[0].type).toBe('ML')
  })
})
