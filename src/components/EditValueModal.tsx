'use client'

import { useState } from 'react'
import type { RefValueItem } from '@/lib/types'

const UNIT_OPTIONS = ['GPa', 'eV', 'eV/atom', 'Å', 'K']

interface EditValueModalProps {
  isOpen: boolean
  onClose: () => void
  refValue: RefValueItem
  sessionToken: string
  onSuccess: () => void
}

export default function EditValueModal({
  isOpen, onClose, refValue, sessionToken, onSuccess,
}: EditValueModalProps) {
  const [value, setValue] = useState(String(refValue.value))
  const [unit, setUnit] = useState(refValue.unit)
  const [uncertainty, setUncertainty] = useState(
    refValue.uncertainty != null ? String(refValue.uncertainty) : ''
  )
  const [temperature, setTemperature] = useState(
    refValue.temperature != null ? String(refValue.temperature) : '300'
  )
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isOpen) return null

  async function handleSave() {
    if (!value.trim()) {
      setError('值不能为空')
      return
    }
    if (!reason.trim()) {
      setError('修正原因不能为空')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const res = await fetch(`/api/admin/reference-values/${refValue.id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionToken}`,
        },
        body: JSON.stringify({
          value: parseFloat(value),
          unit,
          uncertainty: uncertainty.trim() ? parseFloat(uncertainty) : null,
          temperature: temperature.trim() ? parseFloat(temperature) : 300,
          reason: reason.trim(),
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '保存失败')
      onSuccess()
      onClose()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-gray-800 border border-gray-700 rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <h2 className="text-lg font-bold text-white mb-4">
          修正参考值
          <span className="ml-2 text-sm font-normal text-gray-400">
            {refValue.element_system} / {refValue.phase ?? '-'} / {refValue.property}
          </span>
        </h2>

        {error && (
          <div className="mb-4 px-3 py-2 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-xs">
            {error}
          </div>
        )}

        <div className="space-y-4">
          {/* Value */}
          <div>
            <label className="block text-xs text-gray-400 mb-1">值 *</label>
            <input
              type="number"
              step="any"
              value={value}
              onChange={e => setValue(e.target.value)}
              placeholder={String(refValue.value)}
              className="w-full px-3 py-2 bg-gray-900 border border-gray-600 rounded-lg text-white text-sm focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Unit + Uncertainty row */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">单位</label>
              <select
                value={unit}
                onChange={e => setUnit(e.target.value)}
                className="w-full px-3 py-2 bg-gray-900 border border-gray-600 rounded-lg text-white text-sm focus:outline-none focus:border-blue-500"
              >
                {UNIT_OPTIONS.map(u => (
                  <option key={u} value={u}>{u}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">不确定度</label>
              <input
                type="number"
                step="any"
                value={uncertainty}
                onChange={e => setUncertainty(e.target.value)}
                placeholder={refValue.uncertainty != null ? String(refValue.uncertainty) : '可选'}
                className="w-full px-3 py-2 bg-gray-900 border border-gray-600 rounded-lg text-white text-sm focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          {/* Temperature */}
          <div>
            <label className="block text-xs text-gray-400 mb-1">温度 (K)</label>
            <input
              type="number"
              step="any"
              value={temperature}
              onChange={e => setTemperature(e.target.value)}
              placeholder="300"
              className="w-full px-3 py-2 bg-gray-900 border border-gray-600 rounded-lg text-white text-sm focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Reason */}
          <div>
            <label className="block text-xs text-gray-400 mb-1">修正原因 *</label>
            <textarea
              value={reason}
              onChange={e => setReason(e.target.value)}
              placeholder="请说明修正依据..."
              rows={3}
              className="w-full px-3 py-2 bg-gray-900 border border-gray-600 rounded-lg text-white text-sm focus:outline-none focus:border-blue-500 resize-none"
            />
          </div>
        </div>

        {/* Buttons */}
        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg border border-gray-600 text-gray-300 hover:bg-gray-700 transition"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
