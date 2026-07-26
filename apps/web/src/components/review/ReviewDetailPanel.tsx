/**
 * ReviewDetailPanel — slide-over panel with split view for reviewing items.
 *
 * Left side: original source paragraph with highlighting.
 * Right side: structured extracted data.
 * Bottom: action buttons (approve / reject / needs_revision).
 *
 * NFM-1874
 */

'use client'

import { useState, useCallback } from 'react'
import { SourceProvenancePanel } from '@/components/review/SourceProvenancePanel'

// ── Types ──────────────────────────────────────────────────────────────

export interface ReviewDetailData {
  readonly id: string
  readonly item_type: string
  readonly item_data: Record<string, unknown>
  readonly confidence: number
  readonly review_status: string
  readonly source: {
    readonly paragraph: string | null
    readonly page: number | null
    readonly doi: string | null
  } | null
  readonly created_at: string
}

interface ReviewDetailPanelProps {
  readonly item: ReviewDetailData | null
  readonly loading?: boolean
  readonly onAction: (id: string, action: 'approve' | 'reject' | 'needs_revision', note?: string) => void
  readonly onClose: () => void
}

// ── Helpers ────────────────────────────────────────────────────────────

function formatItemData(data: Record<string, unknown>): Array<{ key: string; value: string }> {
  const entries: Array<{ key: string; value: string }> = []
  for (const [key, value] of Object.entries(data)) {
    if (value == null) continue
    const displayValue = typeof value === 'object' ? JSON.stringify(value) : String(value)
    entries.push({ key, value: displayValue })
  }
  return entries
}

// ── Component ──────────────────────────────────────────────────────────

export function ReviewDetailPanel({ item, loading, onAction, onClose }: ReviewDetailPanelProps) {
  const [note, setNote] = useState('')
  const [pendingAction, setPendingAction] = useState<'approve' | 'reject' | 'needs_revision' | null>(null)

  const handleAction = useCallback(
    async (action: 'approve' | 'reject' | 'needs_revision') => {
      if (!item) return
      setPendingAction(action)
      try {
        await onAction(item.id, action, note || undefined)
      } finally {
        setNote('')
        setPendingAction(null)
      }
    },
    [item, note, onAction],
  )

  if (!item) return null

  const dataEntries = formatItemData(item.item_data)
  const extractedValue = item.item_data?.value != null ? String(item.item_data.value) : item.item_data?.property_name as string | undefined

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40 transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-3xl bg-gray-900 border-l border-gray-700 z-50 overflow-y-auto shadow-2xl">
        {/* Header */}
        <div className="sticky top-0 bg-gray-900 border-b border-gray-700 px-6 py-4 flex items-center justify-between z-10">
          <h2 className="text-lg font-semibold text-gray-100">
            复核详情
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 transition-colors p-1"
            aria-label="关闭"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Split view */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-6">
          {/* Left: Source */}
          <div>
            <h3 className="text-sm font-medium text-gray-400 mb-3 uppercase tracking-wider">
              原文段落
            </h3>
            <SourceProvenancePanel
              source={item.source}
              extractedValue={extractedValue}
            />
          </div>

          {/* Right: Extracted data */}
          <div>
            <h3 className="text-sm font-medium text-gray-400 mb-3 uppercase tracking-wider">
              抽取结果
            </h3>
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-gray-500">类型</span>
                <span className="text-gray-200 font-medium">{item.item_type}</span>
              </div>
              {dataEntries.map(({ key, value }) => (
                <div key={key} className="flex justify-between text-sm">
                  <span className="text-gray-500">{key}</span>
                  <span className="text-gray-200 font-medium text-right max-w-[200px] truncate">
                    {value}
                  </span>
                </div>
              ))}
              <div className="flex justify-between text-sm pt-2 border-t border-gray-700">
                <span className="text-gray-500">置信度</span>
                <span className="text-emerald-400 font-medium">
                  {(item.confidence * 100).toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-500">当前状态</span>
                <span className="text-gray-200 font-medium">{item.review_status}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Note input */}
        <div className="px-6 pb-4">
          <label className="block text-sm text-gray-400 mb-1" htmlFor="review-note">
            审核备注
          </label>
          <textarea
            id="review-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500"
            placeholder="可选：输入审核备注..."
          />
        </div>

        {/* Action bar */}
        <div className="sticky bottom-0 bg-gray-900 border-t border-gray-700 px-6 py-4 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={() => handleAction('needs_revision')}
            disabled={loading || pendingAction !== null}
            className="inline-flex items-center rounded-lg px-4 py-2 text-sm font-medium bg-amber-600 hover:bg-amber-700 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            需修改
          </button>
          <button
            type="button"
            onClick={() => handleAction('reject')}
            disabled={loading || pendingAction !== null}
            className="inline-flex items-center rounded-lg px-4 py-2 text-sm font-medium bg-red-600 hover:bg-red-700 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            驳回
          </button>
          <button
            type="button"
            onClick={() => handleAction('approve')}
            disabled={loading || pendingAction !== null}
            className="inline-flex items-center rounded-lg px-4 py-2 text-sm font-medium bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            通过
          </button>
        </div>
      </div>
    </>
  )
}
