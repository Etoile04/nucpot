import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import OntologyViewerFrame, {
  buildOntologyViewerSrc,
} from "./OntologyViewerFrame";

describe("buildOntologyViewerSrc", () => {
  it("produces the determinate embed src pointing at the vendored corpus", () => {
    expect(buildOntologyViewerSrc()).toBe(
      "/ontology-viewer/index.html?embed=false&data=/ontology-viewer/data/nvl_ontology_data.json"
    );
  });

  it("appends a node deep-link param when a node id is supplied", () => {
    expect(buildOntologyViewerSrc("Material")).toBe(
      "/ontology-viewer/index.html?embed=false&data=/ontology-viewer/data/nvl_ontology_data.json&node=Material"
    );
  });

  it("passes corpus param instead of data when corpus is provided (NFM-610)", () => {
    const src = buildOntologyViewerSrc(undefined, "ontofuel");
    expect(src).toContain("corpus=ontofuel");
    expect(src).not.toContain("data=");
    expect(src).toContain("embed=false");
  });

  it("passes corpus and node together (NFM-610)", () => {
    const src = buildOntologyViewerSrc("mat:UO2", "smirnov2014");
    expect(src).toContain("corpus=smirnov2014");
    expect(src).toContain("node=mat:UO2");
    expect(src).not.toContain("data=");
  });
});

describe("OntologyViewerFrame", () => {
  it("renders an iframe at the vendored, chromeless viewer", () => {
    render(<OntologyViewerFrame />);
    const frame = screen.getByTitle("OntoFuel 本体可视化");
    expect(frame.tagName).toBe("IFRAME");
    const src = frame.getAttribute("src") ?? "";
    expect(src).toContain("/ontology-viewer/index.html");
    expect(src).toContain("embed=false");
    expect(src).toContain("data=/ontology-viewer/data/nvl_ontology_data.json");
    expect(src).not.toContain("node=");
  });

  it("passes ?node= through for deep linking", () => {
    render(<OntologyViewerFrame node="Material" />);
    const frame = screen.getByTitle("OntoFuel 本体可视化");
    expect(frame.getAttribute("src") ?? "").toContain("node=Material");
  });

  it("passes ?corpus= to iframe when corpus prop is provided (NFM-610)", () => {
    render(<OntologyViewerFrame corpus="ontofuel" />);
    const frame = screen.getByTitle("OntoFuel 本体可视化");
    const src = frame.getAttribute("src") ?? "";
    expect(src).toContain("corpus=ontofuel");
    expect(src).not.toContain("data=");
  });

  it("enforces the iframe height contract so it never collapses below 600px", () => {
    const { container } = render(<OntologyViewerFrame />);
    const frame = container.querySelector("iframe");
    expect(frame).not.toBeNull();
    const style = frame?.getAttribute("style") ?? "";
    const match = style.match(/min-height:\s*(\d+)px/i);
    const minPx = match ? Number(match[1]) : 0;
    expect(minPx).toBeGreaterThanOrEqual(600);
  });

  it("loads lazily and supports fullscreen", () => {
    render(<OntologyViewerFrame />);
    const frame = screen.getByTitle(
      "OntoFuel 本体可视化"
    ) as HTMLIFrameElement;
    expect(frame.getAttribute("loading")).toBe("lazy");
    expect(frame.hasAttribute("allowfullscreen")).toBe(true);
  });

  it("renders a shareable material-records link when recordRef is provided (Phase 2 NFM-267)", () => {
    render(
      <OntologyViewerFrame
        node="mat:UO2"
        recordRef="/materials/UO2?corpus=smirnov2014"
      />
    );
    const link = screen.getByText("View material records →");
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe(
      "/materials/UO2?corpus=smirnov2014"
    );
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer noopener");
  });

  it("omits the record link for pre-record_ref data sources (Phase 0 static corpus)", () => {
    render(<OntologyViewerFrame node="mat:UO2" />);
    expect(
      screen.queryByText("View material records →")
    ).not.toBeInTheDocument();
  });
});
