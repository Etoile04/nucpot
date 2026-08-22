import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const viewerRoot = resolve(__dirname, "../../../public/ontology-viewer");

function readViewerEntryBundle(): string {
  const indexHtml = readFileSync(resolve(viewerRoot, "index.html"), "utf8");
  const scriptPath = indexHtml.match(
    /src="([^"]*\/main\.[^"]+\.js(?:\?[^"]*)?)"/,
  )?.[1];

  if (!scriptPath) {
    throw new Error(
      "Ontology viewer entry bundle is not declared in index.html",
    );
  }

  // Cache-buster query strings (e.g. ?v=nfm3478t30) are not part of the
  // on-disk path.
  const onDiskPath = scriptPath.replace(/[?#].*$/, "");

  return readFileSync(
    resolve(viewerRoot, onDiskPath.replace(/^\//, "")),
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

  it("does not regress the corpus-index timeout to the old 5s abort (NFM-3478)", () => {
    const bundle = readViewerEntryBundle();

    expect(bundle).toContain("setTimeout(()=>e.abort(),3e4)");
    expect(bundle).not.toContain("abort(),5e3)");
  });
});
