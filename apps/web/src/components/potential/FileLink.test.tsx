import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { FileLink } from "./FileLink"

const PROXY_URL =
  "/api/v1/potentials/14607d0a-1a7b-49fd-9b22-1cd5671864c8/file"

describe("<FileLink> (NFM-4458 contract)", () => {
  describe("empty file_url → 'file missing' state", () => {
    it("renders 文件缺失 when file_url is null", () => {
      render(<FileLink potential={{ file_url: null }} />)
      expect(screen.getByText("文件缺失")).toBeInTheDocument()
      expect(screen.queryByRole("link")).toBeNull()
    })

    it("renders 文件缺失 when file_url is undefined", () => {
      render(<FileLink potential={{}} />)
      expect(screen.getByText("文件缺失")).toBeInTheDocument()
      expect(screen.queryByRole("link")).toBeNull()
    })

    it("renders 文件缺失 when file_url is empty string", () => {
      render(<FileLink potential={{ file_url: "" }} />)
      expect(screen.getByText("文件缺失")).toBeInTheDocument()
      expect(screen.queryByRole("link")).toBeNull()
    })
  })

  describe("proxy URL present → single download anchor", () => {
    it("trusts the canonical proxy URL as href unchanged", () => {
      render(<FileLink potential={{ file_url: PROXY_URL }} />)
      const link = screen.getByRole("link")
      expect(link.getAttribute("href")).toBe(PROXY_URL)
    })

    it("derives filename from extra.file_storage uploads key", () => {
      render(
        <FileLink
          potential={{
            file_url: PROXY_URL,
            extra: {
              file_storage: {
                kind: "uploads",
                key: "14607d0a-1a7b-49fd-9b22-1cd5671864c8.tersoff",
              },
            },
          }}
        />,
      )
      const link = screen.getByRole("link")
      expect(link.getAttribute("download")).toBe(
        "14607d0a-1a7b-49fd-9b22-1cd5671864c8.tersoff",
      )
    })

    it("derives filename from extra.file_storage supabase objects", () => {
      render(
        <FileLink
          potential={{
            file_url: PROXY_URL,
            extra: {
              file_storage: {
                kind: "supabase",
                objects: ["potentials/library/Al_Mendelev_2008.eam.fs"],
              },
            },
          }}
        />,
      )
      const link = screen.getByRole("link")
      expect(link.getAttribute("download")).toBe("Al_Mendelev_2008.eam.fs")
    })

    it("falls back to URL last segment when extra has no storage ref", () => {
      render(<FileLink potential={{ file_url: PROXY_URL }} />)
      const link = screen.getByRole("link")
      expect(link.getAttribute("download")).toBe("file")
    })
  })

  describe("variant prop", () => {
    it("button variant shows a 下载 button", () => {
      render(<FileLink potential={{ file_url: PROXY_URL }} variant="button" />)
      expect(screen.getByRole("button", { name: /下载/ })).toBeInTheDocument()
    })

    it("link variant shows a bare link with the filename as text", () => {
      render(
        <FileLink
          potential={{ file_url: PROXY_URL }}
          variant="link"
        />,
      )
      const link = screen.getByRole("link")
      expect(link.tagName).toBe("A")
      expect(link.textContent).toBeTruthy()
      expect(screen.queryByRole("button")).toBeNull()
    })
  })

  describe("no legacy re-interpretation (NFM-4458 acceptance)", () => {
    it("does not transform Supabase-relative paths (the canonical contract is the proxy URL only)", () => {
      // The frontend trusts the backend canonicalization. A Supabase-relative
      // path that reaches the client is a data-integrity bug, NOT something
      // the FE should silently rewrite.
      const supabaseRelative = "/storage/v1/object/public/potentials/library/x.eam.fs"
      render(<FileLink potential={{ file_url: supabaseRelative }} />)
      const link = screen.getByRole("link")
      expect(link.getAttribute("href")).toBe(supabaseRelative)
    })
  })
})
