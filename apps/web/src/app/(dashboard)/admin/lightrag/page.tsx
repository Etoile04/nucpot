'use client'

import { useEffect, useRef, useState } from 'react'

/**
 * LightRAG WebUI embed page.
 *
 * Mounts the LightRAG sidecar's built-in React SPA in a full-viewport
 * iframe. The rewrite in next.config.ts proxies /lightrag-webui/* to
 * the LightRAG container so all assets, API calls, and WebSocket
 * connections stay same-origin (no CORS issues, no port 9621 exposure).
 *
 * Route: /admin/lightrag
 */
export default function LightRAGWebUIPage() {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const iframe = iframeRef.current
    if (!iframe) return
    const handleLoad = () => setLoading(false)
    iframe.addEventListener('load', handleLoad)
    return () => iframe.removeEventListener('load', handleLoad)
  }, [])

  return (
    <div className="fixed inset-0 z-50 bg-gray-900">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-gray-400 text-sm animate-pulse">
            正在加载知识图谱管理界面…
          </div>
        </div>
      )}
      <iframe
        ref={iframeRef}
        src="/lightrag-api/webui/"
        className="h-full w-full border-0"
        title="LightRAG 知识图谱管理"
        allow="fullscreen"
      />
    </div>
  )
}
