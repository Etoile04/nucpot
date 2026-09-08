/**
 * useFormSubmit state-transition guard tests (NFM-4456).
 *
 * These tests pin the canonical 4-state union (`idle | submitting | success |
 * error`) and exercise every transition in one pass. Any new form that adopts
 * `useFormSubmit` inherits this behavior; if the contract drifts, these
 * tests fail first.
 */
import { describe, it, expect, vi } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useFormSubmit } from "./useFormSubmit"

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

const wrap = makeWrapper()

describe("useFormSubmit — state machine", () => {
  it("starts in idle with no error and no data", () => {
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, { ok: true }>({
          mutationFn: async () => ({ ok: true as const }),
        }),
      { wrapper: wrap },
    )

    expect(result.current.status).toBe("idle")
    expect(result.current.isIdle).toBe(true)
    expect(result.current.isSubmitting).toBe(false)
    expect(result.current.isSuccess).toBe(false)
    expect(result.current.isError).toBe(false)
    expect(result.current.error).toBeNull()
    expect(result.current.data).toBeUndefined()
  })

  it("idle → submitting → success: error stays null, data flows through", async () => {
    const onSuccess = vi.fn()
    let resolveMutation!: (value: { ok: true }) => void
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, { ok: true }>({
          mutationFn: () =>
            new Promise<{ ok: true }>((resolve) => {
              resolveMutation = resolve
            }),
          onSuccess,
        }),
      { wrapper: wrap },
    )

    let promise!: Promise<{ ok: true }>
    act(() => {
      promise = result.current.submit({ x: 1 })
    })

    // `submitting` must be observable while the mutation is in-flight. Use
    // waitFor because TanStack Query schedules the isPending flip on a
    // microtask boundary that races with synchronous state observation.
    await waitFor(() => expect(result.current.status).toBe("submitting"))
    expect(result.current.isSubmitting).toBe(true)
    expect(result.current.error).toBeNull()

    // Resolve the controlled promise and let React flush the resulting state.
    await act(async () => {
      resolveMutation({ ok: true })
      await promise
    })

    await waitFor(() => expect(result.current.status).toBe("success"))
    expect(result.current.isSuccess).toBe(true)
    expect(result.current.error).toBeNull()
    await waitFor(() => expect(result.current.data).toEqual({ ok: true }))
    expect(onSuccess).toHaveBeenCalledWith({ ok: true }, { x: 1 })
  })

  it("idle → submitting → error: surfaces message via Error.message", async () => {
    const onError = vi.fn()
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, unknown>({
          mutationFn: async () => {
            throw new Error("boom")
          },
          errorFallback: "fallback",
          onError,
        }),
      { wrapper: wrap },
    )

    let caught: unknown = null
    await act(async () => {
      try {
        await result.current.submit({ x: 1 })
      } catch (e) {
        caught = e
      }
    })

    await waitFor(() => expect(result.current.status).toBe("error"))
    expect(result.current.isError).toBe(true)
    expect(result.current.error).toBe("boom")
    expect(caught).toBeInstanceOf(Error)
    expect((caught as Error).message).toBe("boom")
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it("idle → submitting → error (non-Error throw) → uses errorFallback", async () => {
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, unknown>({
          mutationFn: async () => {
            // Throw a plain object so neither the Error nor string branch of
            // toMessage matches — the fallback is the only source of truth.
            // Note: the previous `@typescript-eslint/no-throw-literal` disable
            // was removed because that rule no longer exists in the current
            // typescript-eslint; assigning to `unknown` keeps the linter quiet
            // without referencing an undefined rule.
            const thrown: unknown = { code: "E_FOO" }
            throw thrown
          },
          errorFallback: "网络错误",
        }),
      { wrapper: wrap },
    )

    await act(async () => {
      try {
        await result.current.submit({ x: 1 })
      } catch {
        // expected
      }
    })

    await waitFor(() => expect(result.current.status).toBe("error"))
    expect(result.current.error).toBe("网络错误")
  })

  it("failWith() forces error state without touching the mutation", () => {
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, { ok: boolean }>({
          mutationFn: async ({ x }) => ({ ok: x === 1 }),
        }),
      { wrapper: wrap },
    )

    act(() => result.current.failWith("请填写标题"))
    expect(result.current.status).toBe("error")
    expect(result.current.error).toBe("请填写标题")
    expect(result.current.mutation.isError).toBe(false)
  })

  it("failWith() + reset() clears validation error back to idle", () => {
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, { ok: boolean }>({
          mutationFn: async ({ x }) => ({ ok: x === 1 }),
        }),
      { wrapper: wrap },
    )

    act(() => result.current.failWith("请填写标题"))
    expect(result.current.status).toBe("error")

    act(() => result.current.reset())
    expect(result.current.status).toBe("idle")
    expect(result.current.error).toBeNull()
  })

  it("reset() after a server error returns to idle", async () => {
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, unknown>({
          mutationFn: async () => {
            throw new Error("server down")
          },
        }),
      { wrapper: wrap },
    )

    await act(async () => {
      try {
        await result.current.submit({ x: 1 })
      } catch {
        // expected
      }
    })
    await waitFor(() => expect(result.current.status).toBe("error"))

    act(() => result.current.reset())
    expect(result.current.status).toBe("idle")
    expect(result.current.error).toBeNull()
  })

  it("success → new submit → success again: data is replaced", async () => {
    let counter = 0
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, { n: number }>({
          mutationFn: async () => {
            await new Promise((r) => setTimeout(r, 5))
            return { n: ++counter }
          },
        }),
      { wrapper: wrap },
    )

    let first!: { n: number }
    await act(async () => {
      first = await result.current.submit({ x: 1 })
    })
    expect(first).toEqual({ n: 1 })
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    let second!: { n: number }
    await act(async () => {
      second = await result.current.submit({ x: 2 })
    })
    expect(second).toEqual({ n: 2 })
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }))
    expect(result.current.status).toBe("success")
  })
})

describe("useFormSubmit — consumer wiring smoke tests", () => {
  it("exposes the mutation handle for advanced consumers", () => {
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, { ok: boolean }>({
          mutationFn: async ({ x }) => ({ ok: x === 1 }),
        }),
      { wrapper: wrap },
    )
    expect(result.current.mutation).toBeDefined()
    expect(typeof result.current.mutation.mutateAsync).toBe("function")
  })

  it("runs with default errorFallback when none provided", async () => {
    const { result } = renderHook(
      () =>
        useFormSubmit<{ x: number }, unknown>({
          mutationFn: async () => {
            throw 42
          },
        }),
      { wrapper: wrap },
    )

    await act(async () => {
      try {
        await result.current.submit({ x: 1 })
      } catch {
        // expected
      }
    })
    await waitFor(() => expect(result.current.status).toBe("error"))
    expect(result.current.error).toBe("提交失败")
  })
})
