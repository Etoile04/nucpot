import type { Metadata } from "next"
import { ApiDocsFrame } from "./ApiDocsFrame"

// NFM-4991 (IA-REF P2): enable the platform-design §1 「API 文档 /api-docs」
// first-level nav entry by reverse-proxying FastAPI's built-in Swagger UI
// under /api-docs/swagger/* (rewrites in next.config.ts). This page renders
// the brand header + a thin Chrome wrapper around the iframe so the embed
// looks intentional rather than a raw Swagger default. The frame is a
// client component so we can fill the viewport height and surface the
// "open in new tab" link next to the title.

export const metadata: Metadata = {
  title: "API 文档 - NucPot",
  description:
    "NFMD 核材料与势函数数据库公开 REST API 文档(基于 FastAPI 自动生成的 OpenAPI 3.1 规范,Swagger UI)。",
}

export default function ApiDocsPage() {
  // Use a div rather than <main> — the root layout already renders the
  // semantic <main> wrapper around children, and nesting <main> is invalid
  // HTML. The parent <main> is flex-1 + overflow-y-auto, so we let this
  // wrapper take the full available height and let the iframe fill it.
  return (
    <div className="flex flex-col h-full">
      <header
        className="px-6 py-4 border-b flex-shrink-0"
        style={{
          background: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <div className="max-w-[1200px] mx-auto flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-gray-100">API 文档</h1>
            <p className="text-sm text-gray-400 mt-0.5">
              核燃料与材料物性数据库(NFMD)公开 REST API。共 19 个端点分组,
              遵循 <code className="text-gray-300">{"{ success, data, error }"}</code>{" "}
              统一信封与 RFC 7807 错误风格。
            </p>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <a
              href="/api-docs/swagger/openapi.json"
              className="px-3 py-1.5 rounded border border-gray-600 text-gray-200 hover:border-blue-400 hover:text-blue-300 transition"
              target="_blank"
              rel="noopener"
            >
              OpenAPI JSON
            </a>
            <a
              href="/api-docs/swagger"
              className="px-3 py-1.5 rounded border border-gray-600 text-gray-200 hover:border-blue-400 hover:text-blue-300 transition"
              target="_blank"
              rel="noopener"
            >
              新窗口打开
            </a>
          </div>
        </div>
      </header>
      <ApiDocsFrame />
    </div>
  )
}