import { NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)

  const type = searchParams.get('type')
  const elements = searchParams.get('elements')
  const query = searchParams.get('q')
  const page = parseInt(searchParams.get('page') || '1')
  const limit = parseInt(searchParams.get('limit') || '20')
  const offset = (page - 1) * limit

  let dbQuery = supabase
    .from('potentials')
    .select('*', { count: 'exact' })
    .eq('status', 'published')
    .order('updated_at', { ascending: false })
    .range(offset, offset + limit - 1)

  if (type) {
    dbQuery = dbQuery.eq('type', type)
  }

  if (elements) {
    const elemArray = elements.split(',').map(e => e.trim())
    dbQuery = dbQuery.overlaps('elements', elemArray)
  }

  if (query) {
    dbQuery = dbQuery.textSearch('search_vector', query)
  }

  const irradiation = searchParams.get('irradiation')
  const hasDefect = searchParams.get('hasDefect')
  const hasLiquid = searchParams.get('hasLiquid')
  const validationLevel = searchParams.get('validationLevel')
  const tempMin = searchParams.get('tempMin')
  const tempMax = searchParams.get('tempMax')

  if (irradiation === 'true') {
    dbQuery = dbQuery.contains('extra', { irradiationRelevant: true })
  }

  if (hasDefect === 'true') {
    dbQuery = dbQuery.contains('extra', { hasDefectData: true })
  }

  if (hasLiquid === 'true') {
    dbQuery = dbQuery.contains('extra', { hasLiquidPhase: true })
  }

  if (validationLevel && validationLevel !== 'all') {
    dbQuery = dbQuery.contains('extra', { validationLevel })
  }

  const { data, count, error } = await dbQuery

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  return NextResponse.json({
    potentials: data,
    total: count,
    page,
    limit,
    totalPages: Math.ceil((count || 0) / limit)
  })
}
