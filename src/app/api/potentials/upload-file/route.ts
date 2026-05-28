import { NextRequest, NextResponse } from 'next/server'
import { supabase, supabaseAdmin } from '@/lib/supabase'

const ALLOWED_EXTENSIONS = [
  '.eam.alloy', '.eam.fs', '.eam', '.setfl', '.meam', '.param',
  '.table', '.mtp', '.snap', '.json', '.txt', '.zip', '.tar.gz', '.gz',
  '.reaxff', '.tersoff', '.sw', '.bop', '.comb', '.lj', '.dp',
]
const MAX_SIZE = 50 * 1024 * 1024 // 50MB

function getAllowedMimeType(fileName: string): string {
  const ext = fileName.toLowerCase()
  if (ext.endsWith('.zip')) return 'application/zip'
  if (ext.endsWith('.tar.gz') || ext.endsWith('.gz')) return 'application/gzip'
  if (ext.endsWith('.json')) return 'application/json'
  return 'application/octet-stream'
}

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

  // 2. Parse multipart form
  const formData = await request.formData()
  const file = formData.get('file') as File | null
  const potentialId = formData.get('potential_id') as string | null
  if (!file) {
    return NextResponse.json({ error: 'No file provided' }, { status: 400 })
  }

  // 3. Validate extension
  const fileName = file.name.toLowerCase()
  const hasValidExt = ALLOWED_EXTENSIONS.some(ext => fileName.endsWith(ext))
  if (!hasValidExt) {
    return NextResponse.json(
      { error: `Unsupported file extension. Accepted: ${ALLOWED_EXTENSIONS.join(', ')}` },
      { status: 400 }
    )
  }

  // 4. Validate size
  if (file.size > MAX_SIZE) {
    return NextResponse.json(
      { error: `File too large: ${(file.size / 1024 / 1024).toFixed(1)}MB. Max: 50MB` },
      { status: 400 }
    )
  }

  // 5. Build storage path: {user_id}/{potential_id_or_timestamp}-{sanitized_name}
  const sanitized = file.name.replace(/[^a-zA-Z0-9._-]/g, '_')
  const stem = potentialId || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const storagePath = `${user.id}/${stem}/${sanitized}`

  // 6. Upload to Supabase Storage
  const arrayBuffer = await file.arrayBuffer()
  const contentType = getAllowedMimeType(file.name)

  const client = supabaseAdmin || supabase
  const { data: uploadData, error: uploadError } = await client.storage
    .from('potentials')
    .upload(storagePath, arrayBuffer, {
      contentType,
      upsert: false,
    })

  if (uploadError) {
    return NextResponse.json({ error: uploadError.message }, { status: 500 })
  }

  // 7. Get public URL
  const { data: urlData } = client.storage
    .from('potentials')
    .getPublicUrl(storagePath)

  // 8. If potential_id provided, update the potential record with file_url
  if (potentialId && supabaseAdmin) {
    await supabaseAdmin
      .from('potentials')
      .update({
        file_url: urlData.publicUrl,
        file_size: file.size,
      })
      .eq('id', potentialId)
  }

  return NextResponse.json({
    path: uploadData.path,
    public_url: urlData.publicUrl,
    file_name: file.name,
    file_size: file.size,
  }, { status: 201 })
}
