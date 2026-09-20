import type { Metadata } from "next"
import { Alert } from "antd"
import Link from "next/link"

export const metadata: Metadata = {
  title: "API 文档 - NucPot",
  description:
    "NFMD 公共 REST API 文档：势函数、材料、文献、知识图谱、本体的检索与下载接口。",
}

// NFM-4991 (IA-REFACTOR P2): the /api-docs block reuses the FastAPI
// Swagger UI mounted at /docs (see next.config.ts docsRewrites). We
// deliberately embed the live explorer instead of re-authoring
// endpoint descriptions in markdown — keeping the spec source-of-truth
// in one place (the FastAPI openapi_tags + endpoint docstrings).
// "use client" is intentionally omitted: this is a server component
// so metadata is set during static generation.

export default function ApiDocsPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-900 to-gray-800 text-white">
      <main className="mx-auto max-w-6xl px-6 py-10 space-y-8">
        <header className="space-y-3">
          <h1 className="text-3xl font-bold">API 文档</h1>
          <p className="text-gray-300 leading-relaxed">
            NFMD 公共 REST API。所有数据检索接口对匿名访问开放；写操作(上传、审核、抽取)
            需要 <code className="px-1.5 py-0.5 rounded bg-gray-800 text-blue-300">editor</code>{" "}
            或 <code className="px-1.5 py-0.5 rounded bg-gray-800 text-blue-300">admin</code>{" "}
            角色。
          </p>
          <div className="flex flex-wrap gap-3 text-sm">
            <Link
              href="/openapi.json"
              className="px-3 py-1.5 rounded border border-gray-600 hover:border-blue-400 hover:text-blue-300 transition"
              prefetch={false}
            >
              下载 OpenAPI 3 spec (JSON)
            </Link>
            <a
              href="/docs"
              target="_blank"
              rel="noreferrer"
              className="px-3 py-1.5 rounded border border-gray-600 hover:border-blue-400 hover:text-blue-300 transition"
            >
              在新标签页打开 Swagger UI ↗
            </a>
          </div>
        </header>

        <Alert
          type="info"
          showIcon
          message="认证与限速"
          description={
            <ul className="list-disc list-inside space-y-1 text-sm">
              <li>
                匿名读: <code>GET /api/v1/*</code> 的只读端点(势函数/材料/文献/属性/本体/图谱)。
              </li>
              <li>
                写操作: 需 <code>Authorization: Bearer &lt;jwt&gt;</code>。未登录访问写端点返回 401,
                非授权角色访问返回 403。
              </li>
              <li>
                限速: 默认 60 req/min/IP。批量端点(<code>/reference-values/bulk</code>、
                <code>/admin/*</code>)有更严格的策略。
              </li>
              <li>
                CORS: 浏览器同源请求通过 Next.js rewrite 命中 <code>/api/*</code> → 无 CORS 问题。
                跨域直接调用 API 域名需要预检通过。
              </li>
            </ul>
          }
        />

        {/* Live Swagger UI iframe. The src is /docs which is rewritten
            to the FastAPI backend by docsRewrites. Allow-same-origin is
            needed because Swagger UI fetches /openapi.json from its
            own origin. Sandbox-restricted script execution is fine —
            Swagger UI is self-contained and does not need popups or
            top-navigation. */}
        <section className="rounded-lg overflow-hidden border border-gray-700 bg-gray-900">
          <iframe
            title="NFMD API Swagger UI"
            src="/docs"
            className="w-full"
            style={{ height: "min(82vh, 900px)" }}
            // The Swagger UI HTML shell loads its own JS+CSS from the
            // same origin so allow-same-origin is required; the rest
            // of the sandbox is locked down.
            sandbox="allow-scripts allow-same-origin allow-forms"
            referrerPolicy="strict-origin-when-cross-origin"
          />
        </section>

        <section className="text-sm text-gray-400 space-y-2">
          <h2 className="text-lg font-semibold text-gray-200">反馈</h2>
          <p>
            接口错误或字段含义不明,请通过{" "}
            <Link href="/feedback" className="text-blue-400 hover:underline">
              反馈页
            </Link>{" "}
            提交,或在 GitHub issue 中描述请求示例与期望响应。
          </p>
        </section>
      </main>
    </div>
  )
}