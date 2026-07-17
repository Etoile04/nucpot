/**
 * Tests for extraction API integration in api-client.ts.
 *
 * Verifies:
 * - V1 extractionApi exports (trigger, getStatus)
 * - V4 extraction re-exports (submitExtractionJob, getExtractionStatus, etc.)
 * - Extraction API types are structurally correct
 */

import type {
  ExtractionTriggerRequest,
  ExtractionTriggerResponse,
  ExtractionStatusResponse,
} from "@/lib/api-client"

import { describe, it, expect, vi, beforeEach } from "vitest"

// ─── Mock fetch ───────────────────────────────────────────────────

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal("fetch", mockFetch)
  vi.resetModules()
  // Clear localStorage
  localStorage.clear()
})

// ─── V1 Extraction API types (structural validation) ─────────────

describe("V1 Extraction API types", () => {
  it("ExtractionTriggerRequest accepts valid payload", () => {
    const payload: ExtractionTriggerRequest = {
      source_reference: "10.1016/j.jnucmat.2020.01.001",
      source_type: "doi",
      element_systems: ["U-O"],
    }
    expect(payload.source_type).toBe("doi")
    expect(payload.element_systems).toEqual(["U-O"])
  })

  it("ExtractionTriggerResponse matches expected shape", () => {
    const response: ExtractionTriggerResponse = {
      job_id: "abc-123",
      source_reference: "10.1016/j.example",
      source_type: "doi",
      status: "queued",
      message: "Extraction job queued successfully.",
    }
    expect(response.job_id).toBe("abc-123")
    expect(response.status).toBe("queued")
  })

  it("ExtractionStatusResponse matches expected shape", () => {
    const status: ExtractionStatusResponse = {
      job_id: "abc-123",
      source_reference: "10.1016/j.example",
      source_type: "doi",
      status: "completed",
      extracted_count: 42,
      staged_count: 38,
      rejected_count: 4,
      error_message: null,
    }
    expect(status.extracted_count).toBe(42)
    expect(status.staged_count).toBe(38)
  })

  it("ExtractionStatusResponse allows optional fields to be undefined", () => {
    const status: ExtractionStatusResponse = {
      job_id: "abc-123",
      source_reference: "10.1016/j.example",
      source_type: "doi",
      status: "failed",
      extracted_count: 0,
      staged_count: 0,
      rejected_count: 0,
      error_message: "Pipeline error",
    }
    expect(status.error_message).toBe("Pipeline error")
  })
})

// ─── V1 extractionApi ─────────────────────────────────────────────

describe("extractionApi.trigger", () => {
  it("calls POST /api/v1/extraction/trigger with correct payload", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        data: {
          job_id: "job-001",
          source_reference: "10.1016/j.test",
          source_type: "doi",
          status: "queued",
          message: "Extraction job queued successfully.",
        },
      }),
    })

    // Dynamic import to get fresh module after mock setup
    const { extractionApi } = await import("@/lib/api-client")
    const result = await extractionApi.trigger({
      source_reference: "10.1016/j.test",
      source_type: "doi",
    })

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/extraction/trigger",
      expect.objectContaining({
        method: "POST",
      }),
    )
    expect(result.job_id).toBe("job-001")
    expect(result.status).toBe("queued")
  })

  it("throws on non-OK response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({
        detail: "Invalid source_type",
      }),
    })

    const { extractionApi } = await import("@/lib/api-client")

    await expect(
      extractionApi.trigger({
        source_reference: "bad",
        source_type: "doi",
      }),
    ).rejects.toThrow("Invalid source_type")
  })
})

describe("extractionApi.getStatus", () => {
  it("calls GET /api/v1/extraction/status/{jobId}", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        data: {
          job_id: "job-001",
          source_reference: "10.1016/j.test",
          source_type: "doi",
          status: "completed",
          extracted_count: 10,
          staged_count: 8,
          rejected_count: 2,
        },
      }),
    })

    const { extractionApi } = await import("@/lib/api-client")
    const result = await extractionApi.getStatus("job-001")

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/extraction/status/job-001",
      expect.objectContaining({
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    )
    expect(result.status).toBe("completed")
    expect(result.extracted_count).toBe(10)
  })

  it("throws 404 when job not found", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({
        detail: "Extraction job 'nonexistent' not found.",
      }),
    })

    const { extractionApi } = await import("@/lib/api-client")

    await expect(
      extractionApi.getStatus("nonexistent"),
    ).rejects.toThrow("not found")
  })
})

// ─── V4 Extraction re-exports ────────────────────────────────────

describe("V4 Extraction API re-exports", () => {
  it("re-exports submitExtractionJob", async () => {
    const mod = await import("@/lib/api-client")
    expect(typeof mod.submitExtractionJob).toBe("function")
  })

  it("re-exports getExtractionStatus", async () => {
    const mod = await import("@/lib/api-client")
    expect(typeof mod.getExtractionStatus).toBe("function")
  })

  it("re-exports getExtractionResults", async () => {
    const mod = await import("@/lib/api-client")
    expect(typeof mod.getExtractionResults).toBe("function")
  })

  it("re-exports browseProperties", async () => {
    const mod = await import("@/lib/api-client")
    expect(typeof mod.browseProperties).toBe("function")
  })

  it("re-exports validateExtractionResults", async () => {
    const mod = await import("@/lib/api-client")
    expect(typeof mod.validateExtractionResults).toBe("function")
  })

  it("re-exports getMaterialSystems", async () => {
    const mod = await import("@/lib/api-client")
    expect(typeof mod.getMaterialSystems).toBe("function")
  })
})

// ─── Existing exports remain intact ────────────────────────────────

describe("Existing api-client exports are preserved", () => {
  it("still exports authApi", async () => {
    const mod = await import("@/lib/api-client")
    expect(mod.authApi).toBeDefined()
    expect(typeof mod.authApi.login).toBe("function")
  })

  it("still exports blogApi", async () => {
    const mod = await import("@/lib/api-client")
    expect(mod.blogApi).toBeDefined()
    expect(typeof mod.blogApi.list).toBe("function")
  })

  it("still exports request function", async () => {
    const mod = await import("@/lib/api-client")
    expect(typeof mod.request).toBe("function")
  })
})
