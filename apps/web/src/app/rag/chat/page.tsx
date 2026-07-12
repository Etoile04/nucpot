'use client'

import { useState, useCallback } from 'react'
import { RagChatPanel } from '@/components/rag/RagChatPanel'
import { ragApi, createRagMessage } from '@/lib/rag-api'
import type { RagMessage } from '@/lib/rag-api'

export default function RagChatPage() {
  const [messages, setMessages] = useState<ReadonlyArray<RagMessage>>([])
  const [conversationId, setConversationId] = useState<string | undefined>(
    undefined,
  )
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = useCallback(
    async (query: string) => {
      setError(null)
      setLoading(true)

      const userMessage = createRagMessage('user', query)
      setMessages((prev) => [...prev, userMessage])

      try {
        const response = await ragApi.query({
          query,
          conversationId,
          topK: 5,
        })

        const assistantMessage = createRagMessage(
          'assistant',
          response.answer,
          response.citations,
        )

        setMessages((prev) => [...prev, assistantMessage])
        setConversationId(response.conversationId)
      } catch (err: unknown) {
        const message =
          err instanceof Error ? err.message : '请求失败，请重试'
        setError(message)
      } finally {
        setLoading(false)
      }
    },
    [conversationId],
  )

  return (
    <main className="flex min-h-screen items-center justify-center p-4 bg-gray-900">
      <div className="w-full max-w-2xl">
        <RagChatPanel
          onSubmit={handleSubmit}
          messages={messages}
          loading={loading}
          error={error}
        />
      </div>
    </main>
  )
}
