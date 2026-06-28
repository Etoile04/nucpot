import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { PotentialLammpsGen } from "./PotentialLammpsGen"
import type { PotentialDetail } from "@/lib/potentials-api"

const baseDetail: PotentialDetail = {
  id: "pot-001",
  name: "Nb-EAM-FS-001",
  display_name: "Nb EAM Finnis-Sinclair",
  type: "EAM",
  format: "fs",
  elements: ["Nb", "Zr"],
  version: "1.0",
  tags: [],
  system_tags: [],
  applicability: { temperatureRange: [300, 800] },
  references: [],
  developers: [],
  verified_props: null,
  sim_software: ["LAMMPS"],
  lammps_config: {
    pair_style: "eam/alloy",
    pair_coeff: "* * NbZr.eam.alloy Nb Zr",
  },
  file_url: undefined,
  extra: {},
}

describe("PotentialLammpsGen", () => {
  it("renders the LAMMPS pair_style from lammps_config", () => {
    render(<PotentialLammpsGen detail={baseDetail} />)

    // Pair style appears in both the commands block and the full script
    expect(screen.getAllByText(/eam\/alloy/).length).toBeGreaterThan(0)
  })

  it("renders empty hint when pair_style is missing", () => {
    const detail: PotentialDetail = {
      ...baseDetail,
      lammps_config: {},
    }
    render(<PotentialLammpsGen detail={detail} />)

    expect(screen.getByText("暂无 LAMMPS 配置信息")).toBeDefined()
  })

  it("includes pair_coeff in the rendered output", () => {
    render(<PotentialLammpsGen detail={baseDetail} />)

    // pair_coeff appears in both the commands block and the full script
    const matches = screen.getAllByText(
      /pair_coeff \* \* NbZr\.eam\.alloy Nb Zr/,
    )
    expect(matches.length).toBeGreaterThanOrEqual(1)
  })
})
