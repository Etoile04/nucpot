import { NextRequest, NextResponse } from 'next/server'
import { supabaseAdmin } from '@/lib/supabase'

export async function POST(request: NextRequest) {
  if (!supabaseAdmin) {
    return NextResponse.json({ error: 'Service role not configured' }, { status: 500 })
  }

  const { email, password, username, fullName } = await request.json()

  if (!email || !password || !username) {
    return NextResponse.json({ error: 'email, password, and username are required' }, { status: 400 })
  }

  // Create user in Supabase Auth
  const { data: authData, error: authError } = await supabaseAdmin.auth.admin.createUser({
    email,
    password,
    email_confirm: true, // auto-confirm for dev
  })

  if (authError) {
    return NextResponse.json({ error: authError.message }, { status: 400 })
  }

  // Create profile
  const { error: profileError } = await supabaseAdmin
    .from('profiles')
    .insert({
      id: authData.user.id,
      username,
      full_name: fullName || null,
      email,
      role: 'contributor',
    })

  if (profileError) {
    // Cleanup: delete the auth user if profile creation fails
    await supabaseAdmin.auth.admin.deleteUser(authData.user.id)
    return NextResponse.json({ error: profileError.message }, { status: 400 })
  }

  return NextResponse.json({ 
    user: { id: authData.user.id, email, username },
    message: 'Registration successful' 
  }, { status: 201 })
}
