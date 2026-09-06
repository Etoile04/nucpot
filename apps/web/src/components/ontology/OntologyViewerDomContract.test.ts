import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const viewerRoot = resolve(__dirname, "../../../public/ontology-viewer");

function readViewerIndex(): string {
  return readFileSync(resolve(viewerRoot, "index.html"), "utf8");
}

/**
 * NFM-4306 (BUG-28) DOM contract for the vendored ontology viewer entry.
 *
 * GitHub issue #1147 regression: the NFM-3478 debug panel shipped in the
 * production /ontology DOM, and the Element.prototype._cyreg setter hook
 * interfered with cytoscape re-mounts (graph froze after corpus/layout
 * switches). These tests pin the hygiene invariants so neither returns.
 */
describe("NFM-4306 (BUG-28): viewer DOM hygiene + mount robustness", () => {
  it("production index.html contains no NFM-3478 debug panel or debug reporter", () => {
    const html = readViewerIndex();

    expect(html).not.toContain("nfm3478-debug");
    expect(html).not.toContain("__NFM3478_DEBUG");
    expect(html).not.toContain("NFM-3478 DEBUG");
  });

  it("does not patch Element.prototype._cyreg (re-mount interference)", () => {
    const html = readViewerIndex();

    expect(html).not.toMatch(/defineProperty\([^;]*["']_cyreg["']/);
    expect(html).not.toContain("originalDescriptor");
  });

  it("ships a friendly init-failure overlay with a retry action", () => {
    const html = readViewerIndex();

    expect(html).toContain("nfm4306-init-error");
    expect(html).toContain("viewerBooted");
    expect(html).toMatch(/重新加载/);
  });

  it("keeps the NFM-3478 visual fix (edge separation + viewport fit)", () => {
    const html = readViewerIndex();

    expect(html).toContain("control-point-step-size");
    expect(html).toContain("applyNfm3478Styles");
    expect(html).toContain("fitViewport");
  });

  it("retains a bounded mount scan, not an unbounded forever interval", () => {
    const html = readViewerIndex();

    expect(html).toContain("SCAN_BUDGET");
    // The scan must self-terminate once cytoscape is captured.
    expect(html).toMatch(/if \(cyFound \|\| scanCount >= SCAN_BUDGET\) clearInterval/);
  });
});
