import { NextRequest, NextResponse } from 'next/server'

/*
  Required Supabase table:

  CREATE TABLE feedback (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES auth.users(id),
    type VARCHAR(32) NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    email VARCHAR(255),
    status VARCHAR(16) DEFAULT 'open',
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
*/

import { supabase } from '@/lib/supabase'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { type, title, description, email } = body

    if (!title || !title.trim()) {
      return NextResponse.json({ error: '标题不能为空' }, { status: 400 })
    }
    if (!type) {
      return NextResponse.json({ error: '请选择反馈类型' }, { status: 400 })
    }

    const { error } = await supabase.from('feedback').insert({
      type,
      title: title.trim(),
      description: description || null,
      email: email || null,
    })

    if (error) {
      console.error('Feedback insert error:', error)
      return NextResponse.json({ error: '提交失败，请稍后重试' }, { status: 500 })
    }

    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: '无效请求' }, { status: 400 })
  }
}
