/**
 * Literature management page — placeholder skeleton.
 *
 * Route: /literature
 * Spec: NFM-1548
 */

import type { Metadata } from 'next'
import { BreadcrumbNav } from '@/components/BreadcrumbNav'

export const metadata: Metadata = {
  title: '文献管理 - NFMD',
  description: '管理与浏览核材料领域相关文献',
}

export default function LiteraturePage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <BreadcrumbNav items={[{ label: '首页', href: '/' }, { label: '文献管理', href: '/literature' }]} />
      <div className="px-4 py-8 max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold text-white mb-6">文献管理</h1>
        <div className="relative mb-6">
          <input
            type="text"
            placeholder="搜索文献标题、作者、DOI..."
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 transition"
            disabled
          />
        </div>
        <div className="rounded-lg border border-dashed border-gray-700 p-12 text-center">
          <p className="text-gray-500 text-sm">
            暂无文献数据。后端 API 对接后此处将展示文献列表。
          </p>
        </div>
      </div>
    </div>
  )
}
