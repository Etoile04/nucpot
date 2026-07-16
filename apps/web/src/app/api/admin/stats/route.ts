import { NextRequest, NextResponse } from 'next/server'
import { verifyAdmin } from '@/lib/verify-admin'
import { supabase, supabaseAdmin } from '@/lib/supabase'

// GET: Admin dashboard stats
export async function GET(request: NextRequest) {
  const result = await verifyAdmin(request)
  if (result.error) {
    return NextResponse.json({ error: result.error }, { status: result.status })
  }

  // Gather stats from Supabase
  const client = supabaseAdmin || supabase

  const [potentials, contributions, users] = await Promise.all([
    client.from('potentials').select('id, type, source', { count: 'exact' }),
    client.from('contributions').select('id, status, action', { count: 'exact' }),
    client.from('profiles').select('id, role', { count: 'exact' }),
  ])

  const stats = {
    totalPotentials: potentials.count || 0,
    potentialsByType: groupBy(potentials.data || [], 'type'),
    potentialsBySource: groupBy(potentials.data || [], 'source'),
    totalContributions: contributions.count || 0,
    pendingContributions: contributions.data?.filter(c => c.status === 'pending').length || 0,
    totalUsers: users.count || 0,
    usersByRole: groupBy(users.data || [], 'role'),
  }

  return NextResponse.json(stats)
}

function groupBy(arr: any[], key: string): Record<string, number> {
  return arr.reduce((acc, item) => {
    const k = item[key] || 'unknown'
    acc[k] = (acc[k] || 0) + 1
    return acc
  }, {})
}
