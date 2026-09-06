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

  it("renders 未验证 badge when verification_status is empty (NFM-4314)", () => {
    render(
      <PotentialOverview
        detail={{
          ...baseDetail,
          verification_status: "" as PotentialDetail["verification_status"],
        }}
      />,
    )
    expect(screen.getByText("未验证")).toBeDefined()
  })
})

describe("PotentialOverview metadata surfacing (NFM-4314)", () => {
  const tiwaryDetail: PotentialDetail = {
    ...baseDetail,
    name: "RigidIon_UO2_Tiwary_2009",
    license: "CC-BY-4.0",
    sim_software: ["LAMMPS", "GULP"],
    source_doi: undefined,
    applicability: {
      temperatureRange: [300, 5000],
      phases: ["solid", "liquid"],
      notes: "适用于 UO2 体系固液相模拟",
    },
    references: [
      {
        doi: "10.1103/PhysRevB.79.094301",
        citation: "Tiwary et al. 2009, Phys. Rev. B",
      },
    ],
    developers: [{ name: "A. Tiwary", affiliation: "University of X" }],
  }

  it("renders license", () => {
    render(<PotentialOverview detail={tiwaryDetail} />)
    expect(screen.getByText("CC-BY-4.0")).toBeDefined()
  })

  it("renders sim_software tags", () => {
    render(<PotentialOverview detail={tiwaryDetail} />)
    expect(screen.getByText("LAMMPS")).toBeDefined()
    expect(screen.getByText("GULP")).toBeDefined()
  })

  it("formats temperature range as 300–5000 K", () => {
    render(<PotentialOverview detail={tiwaryDetail} />)
    expect(screen.getByText("300–5000 K")).toBeDefined()
  })

  it("renders applicability phases and notes", () => {
    render(<PotentialOverview detail={tiwaryDetail} />)
    expect(screen.getByText("solid、liquid")).toBeDefined()
    expect(screen.getByText("适用于 UO2 体系固液相模拟")).toBeDefined()
  })

  it("renders references with citation and DOI link", () => {
    render(<PotentialOverview detail={tiwaryDetail} />)
    expect(screen.getByText(/Tiwary et al\. 2009/)).toBeDefined()
    const doiLink = screen.getByRole("link", {
      name: "(DOI: 10.1103/PhysRevB.79.094301)",
    })
    expect(doiLink.getAttribute("href")).toBe(
      "https://doi.org/10.1103/PhysRevB.79.094301",
    )
  })

  it("falls back to references[0].doi when source_doi is empty", () => {
    render(<PotentialOverview detail={tiwaryDetail} />)
    const doiRowLink = screen.getByRole("link", {
      name: "10.1103/PhysRevB.79.094301",
    })
    expect(doiRowLink.getAttribute("href")).toBe(
      "https://doi.org/10.1103/PhysRevB.79.094301",
    )
  })

  it("prefers source_doi over references[0].doi", () => {
    render(
      <PotentialOverview detail={{ ...tiwaryDetail, source_doi: "10.9999/source" }} />,
    )
    expect(screen.getByRole("link", { name: "10.9999/source" })).toBeDefined()
  })

  it("renders developers with affiliation", () => {
    render(<PotentialOverview detail={tiwaryDetail} />)
    expect(screen.getByText(/A\. Tiwary/)).toBeDefined()
    expect(screen.getByText(/University of X/)).toBeDefined()
  })

  it("renders 未验证 badge alongside surfaced metadata for unverified records", () => {
    render(<PotentialOverview detail={tiwaryDetail} />)
    expect(screen.getByText("未验证")).toBeDefined()
    expect(screen.getByText("CC-BY-4.0")).toBeDefined()
  })
})

describe("PotentialOverview empty-value placeholders (NFM-4314)", () => {
  it("renders — for absent optional fields", () => {
    render(<PotentialOverview detail={baseDetail} />)
    const placeholders = screen.getAllByText("—")
    // baseDetail 已提供描述与元素,其余 12 个可选字段均为空:
    // 格式/版本/许可证/模拟软件/体系/来源/温度范围/相态/DOI/文献引用/开发者/适用性备注
    expect(placeholders.length).toBe(12)
  })

  it("renders phases even when temperatureRange is absent", () => {
    render(
      <PotentialOverview
        detail={{ ...baseDetail, applicability: { phases: ["solid"] } }}
      />,
    )
    expect(screen.getByText("solid")).toBeDefined()
    expect(screen.getAllByText("—").length).toBeGreaterThan(0)
  })
})

describe("PotentialOverview bare-string references (F3 / NFM-4343)", () => {
  it("renders bare citation strings without crashing or filtering", () => {
    const bareRef = "J. Nucl. Mater. 541 (2020) 152421"
    render(
      <PotentialOverview detail={{ ...baseDetail, references: [bareRef] }} />,
    )
    expect(screen.getByText(bareRef)).toBeDefined()
  })

  it("renders mixed dict + bare-string reference lists", () => {
    const bareRef = "Phys. Rev. B 102 (2020) 014101"
    render(
      <PotentialOverview
        detail={{
          ...baseDetail,
          references: [
            { doi: "10.1234/canonical", citation: "Canonical ref" },
            bareRef,
          ],
        }}
      />,
    )
    expect(screen.getByText("Canonical ref")).toBeDefined()
    expect(screen.getByText(bareRef)).toBeDefined()
  })

  it("does not fall back to references[0].doi when entry is a bare string", () => {
    const bareRef = "J. Nucl. Mater. 541 (2020) 152421"
    render(
      <PotentialOverview
        detail={{
          ...baseDetail,
          references: [bareRef],
          source_doi: undefined,
        }}
      />,
    )
    // The DOI row should render as — placeholder, not a doi.org link.
    const doiLinks = screen.queryAllByRole("link", { name: /doi\.org/i })
    expect(doiLinks).toHaveLength(0)
  })
})
