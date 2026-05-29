import '@testing-library/jest-dom/vitest'

/**
 * Shared test utility: creates a mock Supabase chain (from → select/insert/etc → single/eq/etc)
 * that resolves to the given result. Returns `as any` to avoid PostgrestQueryBuilder type mismatch.
 */
export function mockSupabaseChain(result: unknown) {
  const store: Record<string, ReturnType<typeof vi.fn>> = {}
  const mk = () => vi.fn(() => {
    const proxy = new Proxy(store, {
      get(t, p) {
        if (p === 'then') return (res: (v: unknown) => unknown, rej: (v: unknown) => unknown) => Promise.resolve(result).then(res, rej)
        if (!t[p as string]) t[p as string] = mk()
        return t[p as string]
      }
    })
    return proxy
  })
  return new Proxy(store, {
    get(t, p) {
      if (p === 'then') return (res: (v: unknown) => unknown, rej: (v: unknown) => unknown) => Promise.resolve(result).then(res, rej)
      if (!t[p as string]) t[p as string] = mk()
      return t[p as string]
    }
  }) as any // eslint-disable-line @typescript-eslint/no-explicit-any
}
