/** Shared types and constants for the NFM-DB platform. */

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
