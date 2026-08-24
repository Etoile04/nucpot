/**
 * Decision Audit Log Page — immutable decision history.
 *
 * Route: /gap-review/audit
 * Spec: NFM-3708, UX ref NFM-3682 §3.4/§7
 */

'use client'

import { Suspense } from 'react'
import { DecisionAuditLog } from '@/components/gap-review/DecisionAuditLog'

function AuditLogContent() {
  return (
    <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">决策审核日志</h1>
          <p className="text-sm text-gray-400 mt-1">所有审核决策的不可变记录</p>
        </div>
      </div>

      <DecisionAuditLog />
    </main>
  )
}

export default function AuditLogPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center py-24">
        <svg className="animate-spin h-8 w-8 text-emerald-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    }>
      <AuditLogContent />
    </Suspense>
  )
}
