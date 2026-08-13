import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const viewerRoot = resolve(__dirname, "../../../public/ontology-viewer");

function readViewerEntryBundle(): string {
  const indexHtml = readFileSync(resolve(viewerRoot, "index.html"), "utf8");
  const scriptPath = indexHtml.match(/src="([^\"]*\/main\.[^"]+\.js)"/)?.[1];

  if (!scriptPath) {
    throw new Error(
      "Ontology viewer entry bundle is not declared in index.html",
    );
  }

  return readFileSync(
    resolve(viewerRoot, scriptPath.replace(/^\//, "")),
    "utf8",
  );
}

describe("vendored ontology viewer network contract", () => {
  it("allows the production CDN to respond within the viewer data timeout", () => {
    const bundle = readViewerEntryBundle();
    const dataRequest = bundle.match(
      /new AbortController.*?Request timeout \([^)]*\)\. Please check your network\./,
    )?.[0];

    expect(dataRequest).toBeDefined();
    expect(dataRequest).toContain("setTimeout(()=>r.abort(),3e4)");
    expect(dataRequest).toContain("Request timeout (30s)");
  });
});
