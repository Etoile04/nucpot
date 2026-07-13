/**
 * KG Node Detail page route wrapper.
 *
 * The actual rendering lives in NodeDetailContent (a client component)
 * so we can use hooks. This wrapper exists so Next.js App Router has a
 * proper page entry, and to provide a Suspense fallback while params
 * resolve.
 *
 * Route: /kg/nodes/[type]/[id]
 * Spec: NFM-1099
 */

'use client'

import { Suspense } from 'react'
import { Spin } from 'antd'
import { NodeDetailContent } from './NodeDetailContent'

export default function NodeDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center items-center min-h-[400px]">
          <Spin tip="Loading…"><div /></Spin>
        </div>
      }
    >
      <NodeDetailContent />
    </Suspense>
  )
}
