'use client'

import { useState } from 'react'
import type { ReferenceValue } from '@/lib/types'
import DoiValidator from './DoiValidator'

interface EditSourceModalProps {
  item: ReferenceValue
  sessionToken: string
  open: boolean
  onClose: () => void
  onSaved: () => void
}

export default function EditSourceModal({
  item,
  sessionToken,
  open,
  onClose,
  onSaved,
}: EditSourceModalProps) {
  const [source, setSource] = useState(item.source || '')
  const [sourceDoi, setSourceDoi] = useState(item.source_doi || '')
  const [method, setMethod] = useState(item.method || '')
  const [confidence, setConfidence] = useState<'high' | 'medium' | 'low'>(
    item.confidence || 'medium'
  )
  const [reviewNotes, setReviewNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!open) return null

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const body: Record<string, unknown> = {
        source,
        source_doi: sourceDoi || null,
        method,
        confidence,
      }
      if (reviewNotes.trim()) {
        body.review_notes = reviewNotes.trim()
      }

      const res = await fetch(`/api/admin/reference-values/${item.id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionToken}`,
        },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '保存失败')

      // If DOI provided, auto-approve
      if (sourceDoi.trim()) {
        await fetch(`/api/admin/reference-values/${item.id}/approve`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${sessionToken}`,
          },
          body: JSON.stringify({}),
        })
      }

      onSaved()
      onClose()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-gray-800 border border-gray-700 rounded-xl w-full max-w-lg mx-4 p-6">
        <h3 className="text-lg font-semibold text-white mb-4">补充来源</h3>

        {error && (
          <div className="mb-4 px-3 py-2 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
            {error}
          </div>
        )}

        <div className="space-y-4">
          {/* Source */}
          <div>
            <label className="block text-sm text-gray-300 mb-1">来源</label>
            <input
              type="text"
              value={source}
              onChange={e => setSource(e.target.value)}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="例如: Smith et al. 2020"
            />
          </div>

          {/* DOI */}
          <div>
            <label className="block text-sm text-gray-300 mb-1">
              DOI <span className="text-gray-500 text-xs">(填写后自动通过审核)</span>
            </label>
            <div className="flex items-center">
              <input
                type="text"
                value={sourceDoi}
                onChange={e => setSourceDoi(e.target.value)}
                className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="10.1016/j.nimb.2019.04.015"
              />
              <DoiValidator doi={sourceDoi} />
            </div>
          </div>

          {/* Method */}
          <div>
            <label className="block text-sm text-gray-300 mb-1">方法</label>
            <input
              type="text"
              value={method}
              onChange={e => setMethod(e.target.value)}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="例如: DFT, experiment, review"
            />
          </div>

          {/* Confidence */}
          <div>
            <label className="block text-sm text-gray-300 mb-2">置信度</label>
            <div className="flex gap-4">
              {(['high', 'medium', 'low'] as const).map(level => (
                <label
                  key={level}
                  className={`flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded-lg border text-sm transition ${
                    confidence === level
                      ? level === 'high'
                        ? 'bg-green-900/30 border-green-700 text-green-300'
                        : level === 'medium'
                        ? 'bg-yellow-900/30 border-yellow-700 text-yellow-300'
                        : 'bg-red-900/30 border-red-700 text-red-300'
                      : 'bg-gray-700 border-gray-600 text-gray-400 hover:text-gray-200'
                  }`}
                >
                  <input
                    type="radio"
                    name="confidence"
                    value={level}
                    checked={confidence === level}
                    onChange={() => setConfidence(level)}
                    className="sr-only"
                  />
                  {level === 'high' ? '高' : level === 'medium' ? '中' : '低'}
                </label>
              ))}
            </div>
          </div>

          {/* Review Notes */}
          <div>
            <label className="block text-sm text-gray-300 mb-1">
              审核备注 <span className="text-gray-500">(可选)</span>
            </label>
            <textarea
              value={reviewNotes}
              onChange={e => setReviewNotes(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              placeholder="可选的审核说明..."
            />
          </div>
        </div>

        {/* Buttons */}
        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg bg-gray-700 text-gray-300 hover:bg-gray-600 transition"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
