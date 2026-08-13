import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { PotentialOverview } from "./PotentialOverview"
import type { PotentialDetail } from "@/lib/potentials-api"

const baseDetail: PotentialDetail = {
  id: "p1",
  name: "Test Potential",
  type: "EAM",
  elements: ["U"],
  description: "A test potential",
  version: "1.0",
  tags: [],
  system_tags: [],
  applicability: {},
  references: [],
  developers: [],
  verified_props: null,
  sim_software: [],
  lammps_config: {},
  extra: {},
  verification_status: "unverified",
}

describe("PotentialOverview verification_status", () => {
  it("renders unverified status tag", () => {
    render(<PotentialOverview detail={{ ...baseDetail, verification_status: "unverified" }} />)
    expect(screen.getByText("未验证")).toBeDefined()
  })

  it("renders verified status tag", () => {
    render(<PotentialOverview detail={{ ...baseDetail, verification_status: "verified" }} />)
    expect(screen.getByText("已验证")).toBeDefined()
  })

  it("renders failed status tag", () => {
    render(<PotentialOverview detail={{ ...baseDetail, verification_status: "failed" }} />)
    expect(screen.getByText("验证失败")).toBeDefined()
  })

  it("renders pending status tag", () => {
    render(<PotentialOverview detail={{ ...baseDetail, verification_status: "pending" }} />)
    expect(screen.getByText("验证中")).toBeDefined()
  })
})
