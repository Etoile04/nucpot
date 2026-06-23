import { describe, it, expect, vi, beforeEach } from "vitest"
import type { FileInfo, PotentialMetadata } from "./upload-api"

describe("upload-api", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "")
    global.fetch = vi.fn()
  })

  it("submitPotential POSTs metadata to /api/potentials/upload", async () => {
    const fakePotential = {
      id: "abc-123",
      name: "Test Potential",
      type: "EAM",
      elements: ["U"],
      system_name: "U system",
      description: "desc",
      status: "pending",
    }
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ success: true, data: fakePotential }),
    })
    const { submitPotential } = await import("./upload-api")
    const result = await submitPotential({
      name: "Test Potential",
      type: "EAM",
      elements: ["U"],
      system_name: "U system",
      description: "desc",
      license_type: "own_work",
    })
    expect(result.success).toBe(true)
    expect((result as { potential: unknown }).potential).toBeTruthy()
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/potentials/upload",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"name"'),
      }),
    )
  })

  it("submitPotential returns error on 409 duplicate name", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ success: false, error: "Potential name already exists" }),
    })
    const { submitPotential } = await import("./upload-api")
    const result = await submitPotential({
      name: "duplicate",
      type: "EAM",
      elements: ["U"],
      system_name: "X",
      description: "Y",
      license_type: "own_work",
    })
    expect(result.success).toBe(false)
    expect((result as { error: string }).error).toContain("already exists")
  })

  it("submitPotential returns error on 400 validation failure", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ success: false, error: "license_type is required" }),
    })
    const { submitPotential } = await import("./upload-api")
    const result = await submitPotential({ name: "x" } as unknown as PotentialMetadata)
    expect(result.success).toBe(false)
    expect((result as { error: string }).error).toContain("license_type")
  })

  it("uploadPotentialFile POSTs file to /api/potentials/upload-file", async () => {
    const fileResp = {
      file_name: "test.eam.alloy",
      file_url: "/uploads/id123/test.eam.alloy",
      file_hash: "abcdef",
      file_size: 42,
    }
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: fileResp }),
    })
    const { uploadPotentialFile } = await import("./upload-api")
    const file = new File(["data"], "test.eam.alloy", { type: "application/octet-stream" })
    const result = await uploadPotentialFile("id123", file)
    expect(result.success).toBe(true)
    expect(((result as { file_info: FileInfo }).file_info).file_name).toBe("test.eam.alloy")
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/potentials/upload-file?id=id123",
      expect.objectContaining({ method: "POST" }),
    )
  })

  it("uploadPotentialFile returns error on bad extension", async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ success: false, error: "Unsupported file extension" }),
    })
    const { uploadPotentialFile } = await import("./upload-api")
    const file = new File(["data"], "bad.exe")
    const result = await uploadPotentialFile("id123", file)
    expect(result.success).toBe(false)
    expect((result as { error: string }).error).toContain("extension")
  })
})
