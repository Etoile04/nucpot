/**
 * NFM-3359 — literature upload error classification.
 *
 * A no-role user uploading a PDF gets HTTP 403, and the UI must show a
 * permission-specific toast. Classifying that 403 by substring-matching the
 * error message is unsafe: the API's 413 detail embeds the raw byte count
 * (`apps/api/src/nfm_db/api/v1/literature.py:328`), and byte counts such as
 * 54031234 contain the digits "403". These tests pin the status to a numeric
 * field so classification never depends on message text.
 */
import { describe, it, expect, vi, afterEach } from "vitest"

import { literatureApi, uploadErrorStatus } from "../api-client"

/** Stubs global fetch with a single non-ok JSON response. */
function stubFailedUpload(status: number, detail: string | null): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status,
      json: async () => (detail === null ? null : { detail }),
    }),
  )
}

const pdf = () => new File(["%PDF-1.4"], "paper.pdf", { type: "application/pdf" })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("literatureApi.upload error status", () => {
  it("carries status 403 when the server rejects for missing roles", async () => {
    stubFailedUpload(403, 'Requires one of roles: ["admin", "editor"]')

    const err = await literatureApi.upload(pdf()).catch((e: unknown) => e)

    expect(uploadErrorStatus(err)).toBe(403)
  })

  it("carries status 403 when the server sends no detail body", async () => {
    stubFailedUpload(403, null)

    const err = await literatureApi.upload(pdf()).catch((e: unknown) => e)

    expect(uploadErrorStatus(err)).toBe(403)
  })

  it('does not classify a 413 as 403 when the byte count contains "403"', async () => {
    // 54031234 contains the substring "403" — the exact false positive that
    // made `message.includes("403")` show the permission toast for a too-large
    // file. Regression guard for NFM-3359.
    stubFailedUpload(413, "File too large: 54031234 bytes (max 52428800)")

    const err = await literatureApi.upload(pdf()).catch((e: unknown) => e)

    expect(uploadErrorStatus(err)).toBe(413)
    expect(uploadErrorStatus(err)).not.toBe(403)
  })

  it("preserves the server detail and status in the user-facing message", async () => {
    stubFailedUpload(415, "Unsupported file type")

    const err = await literatureApi.upload(pdf()).catch((e: unknown) => e)

    expect(err).toBeInstanceOf(Error)
    expect((err as Error).message).toBe("Unsupported file type (415)")
  })
})

describe("uploadErrorStatus", () => {
  it("returns null for a network error carrying no status", () => {
    expect(uploadErrorStatus(new Error("Failed to fetch"))).toBeNull()
  })

  it("returns null for a non-error value", () => {
    expect(uploadErrorStatus("boom")).toBeNull()
  })
})
