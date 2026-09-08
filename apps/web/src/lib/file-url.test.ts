import { describe, it, expect } from "vitest"
import { resolveFileName, resolveFileUrl } from "./file-url"

const PROXY_URL =
  "/api/v1/potentials/14607d0a-1a7b-49fd-9b22-1cd5671864c8/file"

describe("resolveFileUrl (NFM-4458 trust-proxy contract)", () => {
  it("returns the canonical proxy URL unchanged", () => {
    expect(resolveFileUrl(PROXY_URL)).toBe(PROXY_URL)
  })

  it("returns empty string for null (file-missing sentinel)", () => {
    expect(resolveFileUrl(null)).toBe("")
  })

  it("returns empty string for undefined", () => {
    expect(resolveFileUrl(undefined)).toBe("")
  })

  it("returns empty string for empty string", () => {
    expect(resolveFileUrl("")).toBe("")
  })

  it("does NOT prepend Supabase origin to /storage/v1/ paths", () => {
    // The frontend no longer reconstructs Supabase public URLs — the backend
    // migration 083 already canonicalized every row. A Supabase-relative path
    // reaching the client is a data leak the FE must surface, not paper over.
    expect(
      resolveFileUrl("/storage/v1/object/public/potentials/library/x.eam.fs"),
    ).toBe("/storage/v1/object/public/potentials/library/x.eam.fs")
  })

  it("does NOT prepend /uploads/ to bare filenames", () => {
    expect(resolveFileUrl("foo.eam.fs")).toBe("foo.eam.fs")
  })

  it("does NOT rewrite absolute Supabase URLs", () => {
    const abs =
      "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/library/Al_Mendelev_2008.eam.fs"
    expect(resolveFileUrl(abs)).toBe(abs)
  })

  it("does NOT read NEXT_PUBLIC_SUPABASE_URL (frontend has no Supabase origin responsibility)", () => {
    const prev = process.env.NEXT_PUBLIC_SUPABASE_URL
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co"
    try {
      expect(resolveFileUrl("/storage/v1/object/public/x")).toBe(
        "/storage/v1/object/public/x",
      )
    } finally {
      if (prev === undefined) {
        delete process.env.NEXT_PUBLIC_SUPABASE_URL
      } else {
        process.env.NEXT_PUBLIC_SUPABASE_URL = prev
      }
    }
  })
})

describe("resolveFileName (NFM-4458 — name derived from extra.file_storage)", () => {
  it("derives the name from an uploads storage key", () => {
    expect(
      resolveFileName(PROXY_URL, {
        file_storage: {
          kind: "uploads",
          key: "14607d0a-1a7b-49fd-9b22-1cd5671864c8.tersoff",
        },
      }),
    ).toBe("14607d0a-1a7b-49fd-9b22-1cd5671864c8.tersoff")
  })

  it("derives the name from the first supabase object path", () => {
    expect(
      resolveFileName(PROXY_URL, {
        file_storage: {
          kind: "supabase",
          objects: ["potentials/huda/Ag2S_MTP.mtp"],
        },
      }),
    ).toBe("Ag2S_MTP.mtp")
  })

  it("strips the supabase origin and marker from absolute object URLs", () => {
    expect(
      resolveFileName(PROXY_URL, {
        file_storage: {
          kind: "supabase",
          objects: [
            "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/library/Al_Mendelev_2008.eam.fs",
          ],
        },
      }),
    ).toBe("Al_Mendelev_2008.eam.fs")
  })

  it("falls back to the URL's last segment when storage ref carries no usable name", () => {
    // Canonical proxy URL ends in the literal "file" — the <FileLink> UI
    // surfaces the storage-derived name; this fallback is the safety net.
    expect(resolveFileName(PROXY_URL, {})).toBe("file")
    expect(resolveFileName(PROXY_URL, undefined)).toBe("file")
    expect(resolveFileName(PROXY_URL, null)).toBe("file")
  })
})
