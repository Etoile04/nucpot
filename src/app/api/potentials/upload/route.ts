import { NextRequest, NextResponse } from 'next/server'
import { supabase, supabaseAdmin } from '@/lib/supabase'

const VALID_LICENSE_TYPES = ['own_work', 'author_permission', 'open_license']

export async function POST(request: NextRequest) {
  // 1. Verify auth
  const authHeader = request.headers.get('authorization')
  if (!authHeader?.startsWith('Bearer ')) {
    return NextResponse.json({ error: 'Authentication required' }, { status: 401 })
  }
  const token = authHeader.replace('Bearer ', '')
  const { data: { user }, error: authError } = await supabase.auth.getUser(token)
  if (authError || !user) {
    return NextResponse.json({ error: 'Invalid token' }, { status: 401 })
  }

  // 2. Parse body
  const body = await request.json()
  const {
    name, display_name, type, subtype, format, elements, system_name, system_tags,
    description, applicability, references, developers, lammps_config, tags, extra,
    // Authorization fields
    license_type, license_detail, auth_file_path,
  } = body

  // 3. Validate required fields
  if (!name || !type || !elements?.length || !system_name || !description) {
    return NextResponse.json(
      { error: 'name, type, elements, system_name, and description are required' },
      { status: 400 }
    )
  }

  // 4. Validate authorization
  if (!license_type || !VALID_LICENSE_TYPES.includes(license_type)) {
    return NextResponse.json(
      { error: 'license_type is required (own_work, author_permission, or open_license)' },
      { status: 400 }
    )
  }
  if (license_type === 'author_permission' && !auth_file_path) {
    return NextResponse.json(
      { error: 'Authorization proof file is required when license_type is author_permission' },
      { status: 400 }
    )
  }
  if (license_type === 'open_license' && !license_detail) {
    return NextResponse.json(
      { error: 'License name (e.g. CC-BY-4.0) is required when license_type is open_license' },
      { status: 400 }
    )
  }

  // 5. Check name uniqueness (use admin to bypass RLS)
  const { data: existing } = await supabaseAdmin!
    .from('potentials')
    .select('id')
    .eq('name', name)
    .single()
  if (existing) {
    return NextResponse.json({ error: 'Potential name already exists' }, { status: 409 })
  }

  // 6. Insert potential (requires admin to bypass RLS)
  if (!supabaseAdmin) {
    return NextResponse.json({ error: 'Server configuration error' }, { status: 500 })
  }

  const { data: potential, error: insertError } = await supabaseAdmin
    .from('potentials')
    .insert({
      name,
      display_name: display_name || name,
      type,
      subtype,
      format: format || 'LAMMPS',
      elements,
      system_name,
      system_tags: system_tags || [],
      description,
      applicability: applicability || {},
      references: references || [],
      developers: developers || [],
      verified_props: {},
      sim_software: ['LAMMPS'],
      lammps_config: lammps_config || {},
      source: 'user_contributed',
      tags: tags || [],
      extra: {
        ...extra,
        status: 'pending',
        uploaded_by: user.id,
        validationLevel: 'unverified',
        // Store authorization metadata
        license_type,
        license_detail: license_detail || null,
        auth_file_path: auth_file_path || null,
      },
    })
    .select()
    .single()

  if (insertError) {
    return NextResponse.json({ error: insertError.message }, { status: 400 })
  }

  // 7. Create contribution record with authorization info
  await supabaseAdmin.from('contributions').insert({
    user_id: user.id,
    potential_id: potential.id,
    action: 'upload',
    status: 'pending',
    data: {
      name, type, elements,
      license_type,
      license_detail: license_detail || null,
      auth_file_path: auth_file_path || null,
    },
  })

  return NextResponse.json(
    { potential, message: 'Upload submitted for review' },
    { status: 201 }
  )
}
