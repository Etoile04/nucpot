'use client'

import { useState, useRef, useEffect, useCallback, type FormEvent } from 'react'
import type { RagMessage } from '@/lib/rag-api'
import { CitationCard } from '@/components/rag/CitationCard'
import { TypingIndicator } from '@/components/rag/TypingIndicator'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface RagChatPanelProps {
  readonly onSubmit: (query: string) => void
  readonly messages: ReadonlyArray<RagMessage>
  readonly loading?: boolean
  readonly error?: string | null
  readonly className?: string
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function MessageBubble({ message }: { readonly message: RagMessage }) {
  const isUser = message.role === 'user'
  const baseClasses = isUser
    ? 'ml-auto max-w-[80%] bg-blue-600 text-white rounded-lg px-4 py-2 text-sm'
    : 'mr-auto max-w-[80%] bg-gray-700 text-gray-100 rounded-lg px-4 py-2 text-sm'

  return (
    <li role="article" aria-label={isUser ? '用户消息' : '助手消息'}>
      <div className={baseClasses}>
        <p className="whitespace-pre-wrap">{message.content}</p>
        {message.citations.length > 0 && (
          <div className="mt-2 space-y-2">
            {message.citations.map((citation) => (
              <CitationCard key={citation.id} citation={citation} />
            ))}
          </div>
        )}
      </div>
    </li>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-1 items-center justify-center">
      <p className="text-gray-400 text-sm">请描述您要查询的核材料属性或关系</p>
    </div>
  )
}

function ErrorBanner({ message }: { readonly message: string }) {
  return (
    <div
      className="mx-4 mb-2 rounded-md bg-red-900/50 px-3 py-2 text-sm text-red-300"
      role="alert"
    >
      {message}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function RagChatPanel({
  onSubmit,
  messages,
  loading = false,
  error = null,
  className = '',
}: RagChatPanelProps) {
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, loading, scrollToBottom])

  const handleSubmit = useCallback(
    (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault()
      const trimmed = input.trim()
      if (!trimmed || loading) {
        return
      }
      onSubmit(trimmed)
      setInput('')
    },
    [input, loading, onSubmit],
  )

  const isEmpty = messages.length === 0 && !loading
  const sendDisabled = loading || input.trim().length === 0

  return (
    <div
      className={`bg-gray-800 rounded-lg border border-gray-700 flex flex-col h-[600px] ${className}`}
    >
      {/* Header */}
      <header className="px-4 py-3 border-b border-gray-700 font-semibold text-sm">
        RAG 对话
      </header>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {isEmpty ? (
          <EmptyState />
        ) : (
          <ul
            ref={listRef}
            role="log"
            aria-live="polite"
            aria-label="对话历史"
            className="space-y-4"
          >
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                message={msg}
              />
            ))}
            {loading && (
              <li role="article" aria-label="正在回复">
                <TypingIndicator />
              </li>
            )}
          </ul>
        )}
      </div>

      {/* Error banner */}
      {error && <ErrorBanner message={error} />}

      {/* Input */}
      <div className="px-4 py-3 border-t border-gray-700 flex gap-2">
        <form onSubmit={handleSubmit} className="flex w-full gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入查询内容..."
            className="flex-1 rounded-md border border-gray-600 bg-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            aria-label="查询输入框"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={sendDisabled}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-400"
            aria-label="发送"
          >
            发送
          </button>
        </form>
      </div>

      {/* Slide-up + fade-in animation */}
      <style>{`
        ul[role="log"] > li[role="article"] {
          animation: slideUp 200ms ease-out;
        }
        @media (prefers-reduced-motion: reduce) {
          ul[role="log"] > li[role="article"] {
            animation: none;
          }
        }
        @keyframes slideUp {
          from {
            opacity: 0;
            transform: translateY(8px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
    </div>
  )
}
