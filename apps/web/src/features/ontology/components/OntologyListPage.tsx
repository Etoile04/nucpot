'use client'

import { useCallback } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  useOntologyVersions,
  useDeprecateVersion,
} from '../api'
import { extractCounts } from '../api/types'
import type { OntologyVersion, OntologyVersionStatus } from '../api/types'

const PAGE_SIZE = 20

const STATUS_OPTIONS: { value: OntologyVersionStatus | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'draft', label: '草稿' },
  { value: 'published', label: '已发布' },
  { value: 'deprecated', label: '已弃用' },
]

const STATUS_LABEL: Record<OntologyVersionStatus, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已弃用',
}

const STATUS_COLOR: Record<OntologyVersionStatus, string> = {
  draft: 'var(--ontology-status-draft)',
  published: 'var(--ontology-status-published)',
  deprecated: 'var(--ontology-status-deprecated)',
}

/* ── Sub-components ──────────────────────────────────────── */

function StatusBadge({ status }: { status: OntologyVersionStatus }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
      style={{
        color: STATUS_COLOR[status],
        background: `color-mix(in srgb, ${STATUS_COLOR[status]} 15%, transparent)`,
      }}
      aria-label={`状态: ${STATUS_LABEL[status]}`}
    >
      <span
        className="w-1.5 h-1.5 rounded-full"
        style={{ background: STATUS_COLOR[status] }}
      />
      {STATUS_LABEL[status]}
    </span>
  )
}

function SkeletonRow() {
  return (
    <tr>
      {Array.from({ length: 6 }).map((_, i) => (
        <td key={i} className="py-3 px-4">
          <div
            className="h-4 rounded animate-pulse"
            style={{
              background: 'var(--ontology-bg-elevated)',
              width: i === 0 ? '40%' : '20%',
            }}
          />
        </td>
      ))}
    </tr>
  )
}

function ErrorBanner({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <div
      className="flex items-center gap-3 rounded-lg px-4 py-3 mb-4"
      style={{
        background: 'var(--ontology-danger-bg)',
        border: '1px solid var(--ontology-danger)',
      }}
    >
      <svg
        className="w-5 h-5 shrink-0"
        style={{ color: 'var(--ontology-danger)' }}
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 9v2m0 4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"
        />
      </svg>
      <span style={{ color: 'var(--ontology-danger)' }} className="text-sm">
        无法加载版本列表
      </span>
      <span style={{ color: 'var(--ontology-text-secondary)' }} className="text-sm">
        {message}
      </span>
      <button
        onClick={onRetry}
        className="ml-auto text-sm font-medium"
        style={{
          color: 'var(--ontology-accent)',
          transition: `color var(--ontology-duration-fast) var(--ontology-ease-out)`,
        }}
        onMouseEnter={(e) =>
          (e.currentTarget.style.color = 'var(--ontology-accent-hover)')
        }
        onMouseLeave={(e) =>
          (e.currentTarget.style.color = 'var(--ontology-accent)')
        }
      >
        重试
      </button>
    </div>
  )
}

function EmptyState({
  hasFilters,
  onClearFilters,
}: {
  hasFilters: boolean
  onClearFilters: () => void
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-16">
      <svg
        className="w-16 h-16"
        style={{ color: 'var(--ontology-accent-muted)' }}
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253v-13zM12 2.25A2.25 2.25 0 0 1 14.25 4.5v-1.5A2.25 2.25 0 0 0 12 2.25z"
        />
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M9.75 9.75h4.5"
        />\n        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M9.75 14.25h4.5"
        />
      </svg>
      <p
        className="text-lg font-semibold"
        style={{ color: 'var(--ontology-text-primary)' }}
      >
        {hasFilters ? '未找到匹配的版本' : '尚无本体版本'}
      </p>
      <p
        className="text-sm"
        style={{ color: 'var(--ontology-text-secondary)' }}
      >
        {hasFilters
          ? '尝试调整筛选条件或清除筛选'
          : '创建第一个草稿版本以开始管理本体结构'}
      </p>
      {hasFilters ? (
        <button
          onClick={onClearFilters}
          className="text-sm font-medium"
          style={{ color: 'var(--ontology-accent)' }}
        >
          清除筛选
        </button>
      ) : null}
    </div>
  )
}

function VersionCard({ version }: { version: OntologyVersion }) {
  const { entityCount, relationCount } = extractCounts(version.ontology_data)
  return (
    <div
      className="rounded-lg p-4"
      style={{
        background: 'var(--ontology-bg-surface)',
        border: '1px solid var(--ontology-border)',
      }}
    >
      <div className="flex items-center justify-between mb-2">
        <Link
          href={`/ontology/versions/${version.id}`}
          className="font-mono text-base font-medium"
          style={{
            color: 'var(--ontology-accent)',
            fontFamily: 'var(--ontology-font-mono)',
          }}
        >
          {version.version}
        </Link>
        <StatusBadge status={version.status} />
      </div>
      <p
        className="text-sm"
        style={{
          color: 'var(--ontology-text-secondary)',
          fontFamily: 'var(--ontology-font-mono)',
          fontSize: 'var(--ontology-text-sm)',
        }}
      >
        实体 {entityCount} · 关系 {relationCount}
      </p>
      <p
        className="text-sm mt-1"
        style={{
          color: 'var(--ontology-text-muted)',
          fontSize: 'var(--ontology-text-sm)',
        }}
      >
        {version.created_at.slice(0, 10)}
      </p>
      <div className="flex gap-2 mt-3">
        <Link
          href={`/ontology/versions/${version.id}`}
          className="px-3 py-1.5 rounded-md text-sm font-medium"
          style={{
            background: 'var(--ontology-bg-elevated)',
            color: 'var(--ontology-text-primary)',
          }}
        >
          查看
        </Link>
        {version.status === 'draft' && (
          <Link
            href={`/ontology/versions/${version.id}/edit`}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{
              background: 'var(--ontology-bg-elevated)',
              color: 'var(--ontology-text-primary)',
            }}
          >
            编辑
          </Link>
        )}
      </div>
    </div>
  )
}

/* ── Main page ──────────────────────────────────────────────── */

export function OntologyListPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const currentStatus =
    (searchParams.get('status') as OntologyVersionStatus | 'all') ?? 'all'
  const currentPage = Number(searchParams.get('page') ?? '1')
  const searchQuery = searchParams.get('q') ?? ''

  const hasFilters = currentStatus !== 'all' || searchQuery.length > 0

  const setPage = useCallback(
    (p: number) => {
      const params = new URLSearchParams(searchParams.toString())
      params.set('page', String(p))
      router.push(`/ontology/versions?${params.toString()}`)
    },
    [router, searchParams],
  )

  const setStatus = useCallback(
    (s: string) => {
      const params = new URLSearchParams(searchParams.toString())
      if (s === 'all') params.delete('status')
      else params.set('status', s)
      params.set('page', '1')
      router.push(`/ontology/versions?${params.toString()}`)
    },
    [router, searchParams],
  )

  const clearFilters = useCallback(() => {
    router.push('/ontology/versions')
  }, [router])

  const { data, isLoading, isError, error, refetch } = useOntologyVersions({
    page: currentPage,
    limit: PAGE_SIZE,
    status: currentStatus === 'all' ? undefined : currentStatus,
  })

  const deprecate = useDeprecateVersion()

  const items = data?.items ?? []
  const pages = data?.pages ?? 1

  return (
    <div
      className="mx-auto max-w-6xl px-6 py-10"
      style={{
        background: 'var(--ontology-bg-primary)',
        minHeight: '100vh',
      }}
    >
      {/* Breadcrumbs */}
      <nav
        className="flex items-center gap-1.5 text-sm mb-8"
        style={{
          color: 'var(--ontology-text-muted)',
          fontSize: 'var(--ontology-text-sm)',
        }}
        aria-label="面包屑导航"
      >
        <Link href="/ontology" style={{ color: 'var(--ontology-accent)' }}>
          语料库浏览器
        </Link>
        <span> &gt; </span>
        <span style={{ color: 'var(--ontology-text-secondary)' }}>版本管理</span>
      </nav>

      {/* Heading */}
      <h1
        style={{
          color: 'var(--ontology-text-primary)',
          fontSize: 'var(--ontology-text-2xl)',
          fontWeight: 'var(--ontology-weight-bold)',
          lineHeight: 'var(--ontology-text-2xl-leading)',
          fontFamily: 'var(--ontology-font-display)',
        }}
      >
        本体版本管理
      </h1>
      <p
        className="mt-1 mb-8"
        style={{
          color: 'var(--ontology-text-secondary)',
          fontSize: 'var(--ontology-text-sm)',
          lineHeight: 'var(--ontology-text-sm-leading)',
        }}
      >
        管理和发布本体版本，查看实体类型与关系类型
      </p>

      {/* Filter bar */}
      <div
        className="flex flex-wrap items-center gap-3 mb-6 rounded-lg p-3"
        style={{
          background: 'var(--ontology-bg-surface)',
          border: '1px solid var(--ontology-border)',
        }}
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <input
            type="text"
            placeholder="版本标签搜索..."
            defaultValue={searchQuery}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const params = new URLSearchParams(searchParams.toString())
                if (e.currentTarget.value) params.set('q', e.currentTarget.value)
                else params.delete('q')
                params.set('page', '1')
                router.push(`/ontology/versions?${params.toString()}`)
              }
            }}
            className="w-full max-w-xs rounded-md px-3 py-1.5 text-sm outline-none"
            style={{
              background: 'var(--ontology-bg-input)',
              color: 'var(--ontology-text-primary)',
              border: '1px solid var(--ontology-border)',
            }}
          />
        </div>
        <select
          value={currentStatus}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-md px-3 py-1.5 text-sm outline-none"
          style={{
            background: 'var(--ontology-bg-input)',
            color: 'var(--ontology-text-primary)',
            border: '1px solid var(--ontology-border)',
          }}
          aria-label="状态筛选"
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Content area */}
      {isError ? (
        <ErrorBanner
          message={error?.message ?? '未知错误'}
          onRetry={() => refetch()}
        />
      ) : items.length === 0 && !isLoading ? (
        <EmptyState hasFilters={hasFilters} onClearFilters={clearFilters} />
      ) : (
        <>
          {/* Desktop table */}
          <div
            className="hidden md:block overflow-x-auto rounded-lg"
            style={{
              background: 'var(--ontology-bg-surface)',
              border: '1px solid var(--ontology-border)',
            }}
          >
            <table className="w-full text-left" role="table">
              <thead>
                <tr
                  style={{
                    borderBottom: '1px solid var(--ontology-border)',
                    color: 'var(--ontology-text-secondary)',
                    fontSize: 'var(--ontology-text-sm)',
                    fontWeight: 'var(--ontology-weight-medium)',
                  }}
                >
                  <th scope="col" className="px-4 py-3">版本标签</th>
                  <th scope="col" className="px-4 py-3" style={{ width: '120px' }}>
                    状态
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-right"
                    style={{ width: '100px' }}
                  >
                    实体类型数
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-right"
                    style={{ width: '100px' }}
                  >
                    关系类型数
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 hidden lg:table-cell"
                    style={{ width: '160px' }}
                  >
                    创建时间
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3"
                    style={{ width: '120px' }}
                  >
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {isLoading
                  ? Array.from({ length: 6 }).map((_, i) => (
                      <SkeletonRow key={i} />
                    ))
                  : items.map((v) => {
                      const { entityCount, relationCount } = extractCounts(v.ontology_data)
                      return (
                        <tr
                          key={v.id}
                          style={{
                            borderBottom:
                              '1px solid var(--ontology-border-subtle)',
                            transition: `background var(--ontology-duration-fast) var(--ontology-ease-out)`,
                          }}
                          onMouseEnter={(e) =>
                            (e.currentTarget.style.background =
                              'var(--ontology-bg-elevated)')
                          }
                          onMouseLeave={(e) =>
                            (e.currentTarget.style.background = 'transparent')
                          }
                        >
                          <td className="px-4 py-3">
                            <Link
                              href={`/ontology/versions/${v.id}`}
                              className="font-medium"
                              style={{
                                color: 'var(--ontology-accent)',
                                fontFamily: 'var(--ontology-font-mono)',
                                fontSize: 'var(--ontology-text-base)',
                              }}
                            >
                              {v.version}
                            </Link>
                          </td>
                          <td className="px-4 py-3">
                            <StatusBadge status={v.status} />
                          </td>
                          <td
                            className="px-4 py-3 text-right"
                            style={{
                              color: 'var(--ontology-text-primary)',
                              fontSize: 'var(--ontology-text-base)',
                            }}
                          >
                            {entityCount}
                          </td>
                          <td
                            className="px-4 py-3 text-right"
                            style={{
                              color: 'var(--ontology-text-primary)',
                              fontSize: 'var(--ontology-text-base)',
                            }}
                          >
                            {relationCount}
                          </td>
                          <td
                            className="px-4 py-3 hidden lg:table-cell"
                            style={{
                              color: 'var(--ontology-text-secondary)',
                              fontSize: 'var(--ontology-text-sm)',
                              lineHeight: 'var(--ontology-text-sm-leading)',
                            }}
                          >
                            {v.created_at.slice(0, 10)}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-1">
                              <Link
                                href={`/ontology/versions/${v.id}`}
                                className="p-1.5 rounded-md"
                                style={{ color: 'var(--ontology-text-secondary)' }}
                                aria-label="查看"
                                onMouseEnter={(e) =>
                                  (e.currentTarget.style.color =
                                    'var(--ontology-accent)')
                                }
                                onMouseLeave={(e) =>
                                  (e.currentTarget.style.color =
                                    'var(--ontology-text-secondary)')
                                }
                              >
                                <svg
                                  className="w-4 h-4"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    strokeWidth={2}
                                    d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"
                                  />
                                  <path
                                    strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"
                          />
                        </svg>
                      </Link>
                      {v.status === 'draft' && (
                        <Link
                          href={`/ontology/versions/${v.id}/edit`}
                          className="p-1.5 rounded-md"
                          style={{ color: 'var(--ontology-text-secondary)' }}
                          aria-label="编辑"
                          onMouseEnter={(e) =>
                            (e.currentTarget.style.color =
                              'var(--ontology-accent)')
                          }
                          onMouseLeave={(e) =>
                            (e.currentTarget.style.color =
                              'var(--ontology-text-secondary)')
                          }
                        >
                          <svg
                            className="w-4 h-4"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="m11 5H6a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"
                            />
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"
                            />
                          </svg>
                        </Link>
                      )}
                      {v.status === 'published' && (
                        <button
                          onClick={() => {
                            if (window.confirm(`确定弃用版本 ${v.version}？`)) {
                              deprecate.mutate(v.id, {
                                onSuccess: () => refetch(),
                              })
                            }
                          }}
                          className="p-1.5 rounded-md"
                          style={{ color: 'var(--ontology-text-muted)' }}
                          aria-label="弃用"
                        >
                          <svg
                            className="w-4 h-4"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1 10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                            />
                          </svg>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile card list (visible below md) */}
      <div className="md:hidden flex flex-col gap-3">
        {isLoading
          ? Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="h-28 rounded-lg animate-pulse"
                style={{ background: 'var(--ontology-bg-surface)' }}
              />
            ))
          : items.map((v) => <VersionCard key={v.id} version={v} />)}
      </div>

      {/* Pagination */}
      {!isLoading && pages > 1 && (
        <div
          className="flex items-center justify-center gap-2 mt-6"
          style={{
            color: 'var(--ontology-text-secondary)',
            fontSize: 'var(--ontology-text-sm)',
          }}
        >
          <button
            onClick={() => setPage(Math.max(1, currentPage - 1))}
            disabled={currentPage <= 1}
            className="px-3 py-1.5 rounded-md text-sm"
            style={{
              background:
                currentPage <= 1
                  ? 'transparent'
                  : 'var(--ontology-bg-elevated)',
              color:
                currentPage <= 1
                  ? 'var(--ontology-text-muted)'
                  : 'var(--ontology-text-primary)',
              cursor: currentPage <= 1 ? 'not-allowed' : 'pointer',
              opacity: currentPage <= 1 ? 0.5 : 1,
            }}
          >
            ←
          </button>
          {Array.from({ length: pages }, (_, i) => i + 1).map((p) => (
            <button
              key={p}
              onClick={() => setPage(p)}
              className="px-3 py-1.5 rounded-md text-sm"
              style={{
                background:
                  p === currentPage
                    ? 'var(--ontology-accent)'
                    : 'transparent',
                color:
                  p === currentPage
                    ? 'oklch(15% 0.01 260)'
                    : 'var(--ontology-text-primary)',
                fontWeight: p === currentPage ? 600 : 400,
              }}
            >
              {p}
            </button>
          ))}
          <button
            onClick={() => setPage(Math.min(pages, currentPage + 1))}
            disabled={currentPage >= pages}
            className="px-3 py-1.5 rounded-md text-sm"
            style={{
              background:
                currentPage >= pages
                  ? 'transparent'
                  : 'var(--ontology-bg-elevated)',
              color:
                currentPage >= pages
                  ? 'var(--ontology-text-muted)'
                  : 'var(--ontology-text-primary)',
              cursor: currentPage >= pages ? 'not-allowed' : 'pointer',
              opacity: currentPage >= pages ? 0.5 : 1,
            }}
          >
            →
          </button>
        </div>
      )}
    </>
      )}
    </div>
  )
}
