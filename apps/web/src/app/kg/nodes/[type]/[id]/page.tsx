/**
 * KG Node Detail page route wrapper.
 *
 * The actual rendering lives in NodeDetailContent (a client component)
 * so we can use hooks. This wrapper exists so Next.js App Router has a
 * proper page entry, and to provide a Suspense fallback while params
 * resolve.
 *
 * Route: /kg/nodes/[type]/[id]
 * Spec: NFM-1099, NFM-1548
 */

'use client'

import { Suspense } from 'react'
import { Spin } from 'antd'
import { BreadcrumbNav, type BreadcrumbItem } from '@/components/BreadcrumbNav'
import { NodeDetailContent } from './NodeDetailContent'

const BREADCRUMB_ITEMS: readonly BreadcrumbItem[] = [
  { label: '首页', href: '/' },
  { label: '知识图谱', href: '/kg/explore' },
  { label: '节点详情', href: '' },
]

export default function NodeDetailPage() {
  return (
    <>
      <BreadcrumbNav items={BREADCRUMB_ITEMS} />
      <Suspense
        fallback={
          <div className="flex justify-center items-center min-h-[400px]">
            <Spin tip="Loading…"><div /></Spin>
          </div>
        }
      >
        <NodeDetailContent />
      </Suspense>
    </>
  )
}
