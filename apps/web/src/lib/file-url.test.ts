import { describe, it, expect } from "vitest"
import { resolveFileUrl } from "./file-url"

describe("resolveFileUrl (NFM-3317)", () => {
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
