import { NextRequest, NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

const ALLOWED_TYPES = [
  'application/pdf',
  'image/png',
  'image/jpeg',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
]
const MAX_SIZE = 10 * 1024 * 1024 // 10MB

export async function POST(request: NextRequest) {
  // Verify auth
  const authHeader = request.headers.get('authorization')
  if (!authHeader?.startsWith('Bearer ')) {
    return NextResponse.json({ error: 'Authentication required' }, { status: 401 })
  }
  const token = authHeader.replace('Bearer ', '')
  const { data: { user }, error } = await supabase.auth.getUser(token)
  if (error || !user) {
    return NextResponse.json({ error: 'Invalid token' }, { status: 401 })
  }

  // Parse multipart form
  const formData = await request.formData()
  const file = formData.get('file') as File | null
  if (!file) {
    return NextResponse.json({ error: 'No file provided' }, { status: 400 })
  }

  // Validate file
  if (!ALLOWED_TYPES.includes(file.type)) {
    return NextResponse.json(
      { error: `Unsupported file type: ${file.type}. Accepted: PDF, PNG, JPG, DOC, DOCX` },
      { status: 400 }
    )
  }
  if (file.size > MAX_SIZE) {
    return NextResponse.json(
      { error: `File too large: ${(file.size / 1024 / 1024).toFixed(1)}MB. Max: 10MB` },
      { status: 400 }
    )
  }

  // Upload to Supabase Storage (auth-proofs bucket)
  const ext = file.name.split('.').pop() || 'bin'
  const filePath = `${user.id}/${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`
  const arrayBuffer = await file.arrayBuffer()

  const { data: uploadData, error: uploadError } = await supabase.storage
    .from('auth-proofs')
    .upload(filePath, arrayBuffer, {
      contentType: file.type,
      upsert: false,
    })

  if (uploadError) {
    // If bucket doesn't exist, create it
    if (uploadError.message?.includes('not found') || uploadError.message?.includes('Bucket')) {
      const { error: createError } = await supabase.storage.createBucket('auth-proofs', {
        public: false,
        fileSizeLimit: MAX_SIZE,
        allowedMimeTypes: ALLOWED_TYPES,
      })
      if (createError) {
        return NextResponse.json(
          { error: `Failed to create storage bucket: ${createError.message}` },
          { status: 500 }
        )
      }
      // Retry upload
      const { data: retryData, error: retryError } = await supabase.storage
        .from('auth-proofs')
        .upload(filePath, arrayBuffer, { contentType: file.type, upsert: false })
      if (retryError) {
        return NextResponse.json({ error: retryError.message }, { status: 500 })
      }
      return NextResponse.json({ path: retryData.path }, { status: 201 })
    }
    return NextResponse.json({ error: uploadError.message }, { status: 500 })
  }

  return NextResponse.json({ path: uploadData.path }, { status: 201 })
}
