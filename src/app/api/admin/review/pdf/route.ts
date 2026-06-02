import { NextRequest, NextResponse } from 'next/server'
import { supabase, supabaseAdmin } from '@/lib/supabase'
import { readFile } from 'fs/promises'
import { existsSync } from 'fs'
import path from 'path'

const ZOTERO_MCP_URL = process.env.ZOTERO_MCP_URL || 'http://127.0.0.1:23120/mcp'

// ── MCP helper: call a Zotero MCP tool ──────────────────────────────────────
async function callZoteroMCP(toolName: string, args: Record<string, unknown>) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 15000)
  try {
    const res = await fetch(ZOTERO_MCP_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        method: 'tools/call',
        params: { name: toolName, arguments: args },
        id: Date.now(),
      }),
      signal: controller.signal,
    })

    if (!res.ok) throw new Error(`Zotero MCP HTTP ${res.status}`)

    const json = await res.json()
    if (json.error) throw new Error(`MCP error: ${json.error.message}`)

    const content = json.result?.content || []
    const text = content
      .filter((b: { type: string }) => b.type === 'text')
      .map((b: { text: string }) => b.text)
      .join('\n')
    return text ? JSON.parse(text) : null
  } finally {
    clearTimeout(timeout)
  }
}

// ── Find PDF path for a literature item ─────────────────────────────────────
async function findPdfForItem(itemKey: string): Promise<string | null> {
  const details = await callZoteroMCP('get_item_details', { itemKey })
  if (!details) return null

  const attachments = details.attachments || []
  // Prefer first PDF attachment
  for (const att of attachments) {
    if (att.contentType === 'application/pdf' && att.path) {
      const resolved = att.path.startsWith('/')
        ? att.path
        : path.join(process.env.HOME || '/root', att.path)
      if (existsSync(resolved)) return resolved
    }
  }
  return null
}

// ── Search Zotero for literature matching title / DOI ────────────────────────
async function searchZoteroLiterature(lit: {
  title: string
  doi?: string | null
}): Promise<string | null> {
  // Try DOI first for exact match
  if (lit.doi) {
    const results = await callZoteroMCP('search_library', {
      query: `doi:"${lit.doi}"`,
      limit: 1,
    })
    const items = results?.results || []
    if (items.length > 0) {
      const pdfPath = await findPdfForItem(items[0].key)
      if (pdfPath) return pdfPath
    }
  }

  // Fall back to title search
  const results = await callZoteroMCP('search_library', {
    query: `"${lit.title.substring(0, 120)}"`,
    limit: 5,
  })
  const items = results?.results || []
  for (const item of items) {
    const pdfPath = await findPdfForItem(item.key)
    if (pdfPath) return pdfPath
  }
  return null
}

import { verifyAdmin } from '@/lib/verify-admin'

// ── GET /api/admin/review/pdf?source_file=... ────────────────────────────────
// Returns PDF binary stream or keyword search results
export async function GET(request: NextRequest) {
  // Auth check
  const admin = await verifyAdmin(request)
  if (!admin) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const { searchParams } = new URL(request.url)
  const sourceFile = searchParams.get('source_file')
  const keyword = searchParams.get('keyword')

  if (!sourceFile) {
    return NextResponse.json({ error: 'Missing source_file parameter' }, { status: 400 })
  }

  if (!supabaseAdmin) {
    return NextResponse.json({ error: 'Supabase admin client not configured' }, { status: 500 })
  }

  try {
    // 1. Look up literature records via RPC
    const { data: literature, error: rpcError } = await supabaseAdmin.rpc(
      'review_literature_for_source',
      { p_source_file: sourceFile }
    )

    if (rpcError) throw rpcError
    const litList: Array<{
      id: string
      title: string
      doi: string | null
      authors: string | null
      journal: string | null
      year: number | null
    }> = Array.isArray(literature) ? literature : []

    if (litList.length === 0) {
      return NextResponse.json(
        { error: 'No literature found for source_file', source_file: sourceFile },
        { status: 404 }
      )
    }

    // ── Keyword search in full-text ────────────────────────────────────────
    if (keyword) {
      const paragraphs: Array<{ page: number | null; text: string; highlight: string }> = []

      for (const lit of litList) {
        try {
          // lit.title from RPC is a citation key, not a real title.
          // Use authors + year to find the Zotero item instead.
          const authorQuery = `${lit.authors || ''} ${lit.year || ''}`.trim()
          const searchRes = await callZoteroMCP('search_library', {
            q: authorQuery,
            mode: 'minimal',
          })
          const searchItems = searchRes?.results || []
          if (searchItems.length > 0) {
            try {
              // 2. Full-text search within matched item(s)
              const ftResults = await callZoteroMCP('search_fulltext', {
                q: keyword,
                itemKeys: [searchItems[0].key],
                mode: 'preview',
              })
              // Zotero search_fulltext returns { results: [{ matches: [...] }] }
              const ftItems = ftResults?.results || []
              for (const ftItem of ftItems) {
                const matches = ftItem.matches || []
                for (const m of matches) {
                  const ctx = m.context || ''
                  paragraphs.push({
                    page: m.page ?? null,
                    text: ctx,
                    highlight: ctx.replace(
                      new RegExp(keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'),
                      (match: string) => `【${match}】`
                    ),
                  })
                }
              }
            } catch {
              // Full-text search may fail for items without indexed content
            }
          }
        } catch {
          // Skip this literature entry if search fails
        }
        if (paragraphs.length >= 20) break
      }

      return NextResponse.json({
        source_file: sourceFile,
        keyword,
        literature_count: litList.length,
        paragraphs,
      })
    }

    // ── PDF proxy ─────────────────────────────────────────────────────────
    // Try to find PDF via Zotero for each literature entry
    for (const lit of litList) {
      try {
        const pdfPath = await searchZoteroLiterature(lit)
        if (pdfPath) {
          const buffer = await readFile(pdfPath)
          const filename = path.basename(pdfPath)

          return new NextResponse(buffer, {
            status: 200,
            headers: {
              'Content-Type': 'application/pdf',
              'Content-Disposition': `inline; filename*=UTF-8''${encodeURIComponent(filename)}`,
              'Cache-Control': 'private, max-age=3600',
            },
          })
        }
      } catch {
        // Continue to next literature entry
      }
    }

    return NextResponse.json(
      {
        error: 'No PDF attachment found in Zotero',
        source_file: sourceFile,
        literature_matched: litList.length,
        hint: 'Check Zotero has the corresponding PDF attached',
      },
      { status: 404 }
    )
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: 'PDF proxy error', detail: message }, { status: 500 })
  }
}
