import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import OntologyCorpusBrowser from "./OntologyCorpusBrowser";

// ── Mock next/navigation ─────────────────────────────────────────────────
const mockRouterPush = vi.fn();
let mockSearchParams = new URLSearchParams("");
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
  usePathname: () => "/ontology",
  useSearchParams: () => mockSearchParams,
}));

// ── Mock global.fetch ─────────────────────────────────────────────────────
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

const sampleIndex = {
  corpora: [
    { id: "default", name: "Default", source_digest: "static" },
    { id: "Smirnov2014", name: "Smirnov 2014", source_digest: "dynamic", row_count: 2 },
    { id: "Wang2016", name: "Wang 2016", source_digest: "dynamic", row_count: 2 },
  ],
  default_corpus: "default",
};

beforeEach(() => {
  mockRouterPush.mockReset();
  mockFetch.mockReset();
  mockSearchParams = new URLSearchParams("");
});

describe("OntologyCorpusBrowser", () => {
  it("renders an iframe whose src does NOT pin ?data= (NFM-3325)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => sampleIndex,
    });

    render(<OntologyCorpusBrowser />);
    const frame = (await screen.findByTitle(
      "OntoFuel 本体可视化",
    )) as HTMLIFrameElement;
    const src = frame.getAttribute("src") ?? "";
    expect(src).toContain("/ontology-viewer/index.html");
    expect(src).toContain("embed=false");
    // The whole point of NFM-3325: no static pinning.
    expect(src).not.toContain("data=");
  });

  it("populates the sidebar with the corpus index after fetch", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => sampleIndex,
    });

    render(<OntologyCorpusBrowser />);
    const select = (await screen.findByTestId(
      "ontology-corpus-select",
    )) as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("default");
    expect(options).toContain("Smirnov2014");
    expect(options).toContain("Wang2016");
  });

  it("selects default corpus on initial render", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => sampleIndex,
    });

    render(<OntologyCorpusBrowser />);
    const select = (await screen.findByTestId(
      "ontology-corpus-select",
    )) as HTMLSelectElement;
    expect(select.value).toBe("default");
  });

  it("selects the URL-provided corpus when ?corpus= is supplied", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => sampleIndex,
    });

    render(<OntologyCorpusBrowser corpus="Smirnov2014" />);
    const select = (await screen.findByTestId(
      "ontology-corpus-select",
    )) as HTMLSelectElement;
    expect(select.value).toBe("Smirnov2014");
  });

  it("passes ?corpus= to iframe when corpus prop is set (NFM-610/3325)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => sampleIndex,
    });

    render(<OntologyCorpusBrowser corpus="Wang2016" />);
    const frame = (await screen.findByTitle(
      "OntoFuel 本体可视化",
    )) as HTMLIFrameElement;
    const src = frame.getAttribute("src") ?? "";
    expect(src).toContain("corpus=Wang2016");
    expect(src).not.toContain("data=");
  });

  it("sidebar change pushes ?corpus= to URL while preserving ?node=", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => sampleIndex,
    });
    mockSearchParams = new URLSearchParams("node=mat:UO2");

    render(<OntologyCorpusBrowser node="mat:UO2" />);
    const select = (await screen.findByTestId(
      "ontology-corpus-select",
    )) as HTMLSelectElement;

    fireEvent.change(select, { target: { value: "Smirnov2014" } });

    await waitFor(() =>
      expect(mockRouterPush).toHaveBeenCalledWith(
        expect.stringContaining("corpus=Smirnov2014"),
      ),
    );
    const pushed = mockRouterPush.mock.calls[0]?.[0] ?? "";
    expect(pushed).toContain("node=mat");
    expect(pushed).not.toContain("corpus=default");
  });

  it("selecting default corpus REMOVES ?corpus= from URL (clean default state)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => sampleIndex,
    });
    mockSearchParams = new URLSearchParams("corpus=Smirnov2014");

    render(<OntologyCorpusBrowser corpus="Smirnov2014" />);
    const select = (await screen.findByTestId(
      "ontology-corpus-select",
    )) as HTMLSelectElement;

    fireEvent.change(select, { target: { value: "default" } });

    await waitFor(() =>
      expect(mockRouterPush).toHaveBeenCalledWith("/ontology"),
    );
  });

  it("falls back gracefully when index fetch fails (viewer still works)", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("fetch failed"));

    render(<OntologyCorpusBrowser />);
    const frame = (await screen.findByTitle(
      "OntoFuel 本体可视化",
    )) as HTMLIFrameElement;
    // viewer iframe still rendered (its own manifest path keeps it useful)
    expect(frame.getAttribute("src") ?? "").not.toContain("data=");
  });

  it("shows the empty-state hint when index fetch returns non-OK", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 502 });
    render(<OntologyCorpusBrowser />);
    expect(
      await screen.findByText(/无法连接语料库服务/),
    ).toBeInTheDocument();
  });
});