import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import OntologyViewerFrame, {
  buildOntologyViewerSrc,
} from "./OntologyViewerFrame";

/**
 * NOTE: URLSearchParams.toString() encodes special characters per the URL
 * standard (e.g. / → %2F, : → %3A). All expected URLs use encoded form.
 */

describe("buildOntologyViewerSrc", () => {
  it("omits ?data= when no corpus is provided (NFM-3325) — viewer fetches corpus/index.json and renders its dropdown", () => {
    expect(buildOntologyViewerSrc()).toBe(
      "/ontology-viewer/index.html?embed=false",
    );
    expect(buildOntologyViewerSrc()).not.toContain("data=");
  });

  it("appends a node deep-link param when a node id is supplied", () => {
    expect(buildOntologyViewerSrc("Material")).toBe(
      "/ontology-viewer/index.html?embed=false&node=Material",
    );
  });

  it("passes corpus param when corpus is provided (NFM-610)", () => {
    const src = buildOntologyViewerSrc(undefined, "ontofuel");
    expect(src).toContain("corpus=ontofuel");
    expect(src).not.toContain("data=");
    expect(src).toContain("embed=false");
  });

  it("passes corpus and node together (NFM-610)", () => {
    const src = buildOntologyViewerSrc("mat:UO2", "smirnov2014");
    expect(src).toContain("corpus=smirnov2014");
    expect(src).toContain("node=mat%3AUO2");
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
    // NFM-3325: no data= pinning — corpus dropdown inside viewer is reachable.
    expect(src).not.toContain("data=");
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

  it("fills available flex space without fixed min-height (NFM-1424 fix)", () => {
    const { container } = render(<OntologyViewerFrame />);
    const frame = container.querySelector("iframe");
    expect(frame).not.toBeNull();
    const style = frame?.getAttribute("style") ?? "";
    // Should NOT have a fixed min-height that could overflow the viewport
    expect(style).not.toMatch(/min-height:\s*600/i);
  });

  it("loads lazily and supports fullscreen", () => {
    render(<OntologyViewerFrame />);
    const frame = screen.getByTitle(
      "OntoFuel 本体可视化",
    ) as HTMLIFrameElement;
    expect(frame.getAttribute("loading")).toBe("lazy");
    expect(frame.hasAttribute("allowfullscreen")).toBe(true);
  });

  it("renders a shareable material-records link when recordRef is provided (Phase 2 NFM-267)", () => {
    render(
      <OntologyViewerFrame
        node="mat:UO2"
        recordRef="/materials/UO2?corpus=smirnov2014"
      />,
    );
    const link = screen.getByText("View material records →");
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe(
      "/materials/UO2?corpus=smirnov2014",
    );
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer noopener");
  });

  it("omits the record link for pre-record_ref data sources (Phase 0 static corpus)", () => {
    render(<OntologyViewerFrame node="mat:UO2" />);
    expect(
      screen.queryByText("View material records →"),
    ).not.toBeInTheDocument();
  });

  it("shows a loading hint when loading=true and no recordRef yet (M4 fix)", () => {
    render(<OntologyViewerFrame node="mat:UO2" corpus="ontofuel" loading />);
    expect(
      screen.getByText("Loading material records…"),
    ).toBeInTheDocument();
  });

  it("hides the loading hint once recordRef resolves (M4 fix)", () => {
    render(
      <OntologyViewerFrame
        node="mat:UO2"
        corpus="ontofuel"
        recordRef="/materials/UO2"
        loading={false}
      />,
    );
    expect(
      screen.queryByText("Loading material records…"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("View material records →"),
    ).toBeInTheDocument();
  });

  it("hides the loading hint when loading=false and no recordRef (idle state)", () => {
    render(<OntologyViewerFrame node="mat:UO2" />);
    expect(
      screen.queryByText("Loading material records…"),
    ).not.toBeInTheDocument();
  });
});