import { describe, it, expect } from "vitest"
import { resolveFileUrl } from "./file-url"

describe("resolveFileUrl (NFM-3317 / NFM-4309)", () => {
  it("passes the canonical proxy path through unchanged (NFM-4309)", () => {
    expect(
      resolveFileUrl("/api/v1/potentials/14607d0a-1a7b-49fd-9b22-1cd5671864c8/file"),
    ).toBe("/api/v1/potentials/14607d0a-1a7b-49fd-9b22-1cd5671864c8/file")
  })

  it("passes absolute Supabase object URLs through unchanged (NFM-4309)", () => {
    expect(
      resolveFileUrl(
        "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/library/Al_Mendelev_2008.eam.fs",
      ),
    ).toBe(
      "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/library/Al_Mendelev_2008.eam.fs",
    )
  })

  it("prefixes Supabase Storage paths with the project origin", () => {
    expect(
      resolveFileUrl(
        "/storage/v1/object/public/potentials/huda/W-Ta_FS_2019.eam.fs",
      ),
    ).toBe(
      "https://gzhiqyopzlmnkdzammhx.supabase.co/storage/v1/object/public/potentials/huda/W-Ta_FS_2019.eam.fs",
    )
  })

  it("keeps site-served /uploads/ paths unchanged", () => {
    expect(resolveFileUrl("/uploads/abc-123.eam.alloy")).toBe(
      "/uploads/abc-123.eam.alloy",
    )
  })

  it("assumes bare filenames live under /uploads/", () => {
    expect(resolveFileUrl("foo.eam.fs")).toBe("/uploads/foo.eam.fs")
  })

  it("passes /app/uploads/ paths through unchanged (dead links tracked separately)", () => {
    expect(resolveFileUrl("/app/uploads/ZrNb_starikov.eam.alloy")).toBe(
      "/app/uploads/ZrNb_starikov.eam.alloy",
    )
  })

  it("honors NEXT_PUBLIC_SUPABASE_URL when set", () => {
    const prev = process.env.NEXT_PUBLIC_SUPABASE_URL
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co"
    try {
      expect(resolveFileUrl("/storage/v1/object/public/x")).toBe(
        "https://example.supabase.co/storage/v1/object/public/x",
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
