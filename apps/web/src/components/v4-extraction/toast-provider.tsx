/**
 * ToastProvider -- wraps Ant Design `message.useMessage()` to
 * standardize toast notification usage across V4 extraction pages.
 *
 * Provides bilingual default messages via the `useToast()` hook.
 */

"use client"

import type { ReactNode } from "react"
import { createContext, useContext, useMemo } from "react"
import { message } from "antd"

// ─── Types ──────────────────────────────────────────────────────────

interface ToastApi {
  success: (msg?: string) => void
  error: (msg?: string) => void
  warning: (msg?: string) => void
  info: (msg?: string) => void
}

interface ToastContextValue extends ToastApi {
  contextHolder: ReactNode
}

// ─── Context ────────────────────────────────────────────────────────

const ToastContext = createContext<ToastContextValue | null>(null)

// ─── Defaults ───────────────────────────────────────────────────────

const FALLBACK = {
  success: "操作成功 / Operation successful",
  error: "操作失败 / Operation failed",
  warning: "操作警告 / Operation warning",
  info: "提示 / Information",
} as const

// ─── Provider ──────────────────────────────────────────────────────

interface ToastProviderProps {
  children: ReactNode
}

export function ToastProvider({ children }: ToastProviderProps) {
  const [messageApi, contextHolder] = message.useMessage({
    top: 24,
    duration: 3,
  })

  const toastApi = useMemo<ToastApi>(
    () => ({
      success: (msg?: string) => messageApi.success({ content: msg ?? FALLBACK.success }),
      error: (msg?: string) => messageApi.error({ content: msg ?? FALLBACK.error }),
      warning: (msg?: string) => messageApi.warning({ content: msg ?? FALLBACK.warning }),
      info: (msg?: string) => messageApi.info({ content: msg ?? FALLBACK.info }),
    }),
    [messageApi],
  )

  const contextValue = useMemo<ToastContextValue>(
    () => ({ ...toastApi, contextHolder }),
    [toastApi, contextHolder],
  )

  return (
    <ToastContext.Provider value={contextValue}>
      {contextHolder}
      {children}
    </ToastContext.Provider>
  )
}

// ─── Hook ────────────────────────────────────────────────────────────

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error("useToast must be used within a <ToastProvider>")
  }
  const { contextHolder: _ctx, ...api } = ctx
  return api
}
