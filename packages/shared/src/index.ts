/** Shared types and constants for the NFM-DB platform. */

// Platform i18n service (NFM-4179): locale resolution, interpolation,
// message catalogs, and cross-product disclosure copy.
export * from "./i18n"

/** API response envelope. */
export interface ApiResponse<T> {
  readonly success: boolean
  readonly data?: T
  readonly error?: string
  readonly meta?: {
    readonly total: number
    readonly page: number
    readonly limit: number
  }
}

/** Health check response. */
export interface HealthResponse {
  readonly status: string
}
