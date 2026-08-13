/**
 * Emit a Cobertura coverage.xml for apps/web (NFM-2045, KR-5.2).
 *
 * apps/web contributes ZERO core-module coverage under the KR-5 spec
 * (NFM-2035 §2 ADR-KR5-1). The core set is:
 *
 *   apps/api/src/, apps/api/src/models/, apps/api/alembic_migrations/, packages/
 *
 * No apps/web path is in that set — the only apps/web mention is an
 * *exclusion* (`apps/web/src/components/ui/`, which does not exist in this
 * tree). The one TypeScript core surface, `packages/shared/src/index.ts`,
 * declares nothing but interfaces, so it has no executable lines to cover.
 *
 * So apps/web emits a documented n/a stub rather than real coverage. The
 * stub's two properties the KR-5 aggregator (NFM-2046) relies on:
 *
 *   1. The package name is the recognisable marker (see STUB_MARKER) so the
 *      aggregator can skip this file deliberately.
 *   2. `lines-valid="0"` and `lines-covered="0"` mean line-weighted
 *      aggregation adds nothing to either numerator or denominator — no
 *      false-zero drag on the KR-5 percentage.
 *
 * Also, the output is byte-for-byte deterministic: a fixed `timestamp="0"`
 * keeps `apps/web/coverage.xml` byte-stable across test runs, so running
 * `pnpm test` never dirties the working tree.
 *
 * Honouring ADR-KR5-1's separation of concerns: this script is plain ES
 * modules / no dependencies, so it runs under any Node version available
 * in CI without forcing a vitest load or a coverage-provider install.
 */

import { writeFileSync, mkdirSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = dirname(fileURLToPath(import.meta.url))

/**
 * Marker string the KR-5 aggregator can use to recognise this stub.
 *
 * Kept as a single export so the test and the emitter cannot drift apart.
 */
export const STUB_MARKER = "n/a: no core web paths per spec ADR-KR5-1"

/**
 * Fixed-string Cobertura body. Deterministic on purpose — see header note.
 */
const STUB_BODY = `<?xml version="1.0" ?>
<coverage version="apps-web-stub" timestamp="0" lines-valid="0" lines-covered="0" line-rate="0" branches-covered="0" branches-valid="0" branch-rate="0" complexity="0">
	<!-- ${STUB_MARKER} -->
	<!-- apps/web has no core paths under NFM-2035 §2 ADR-KR5-1. The aggregator
	     (scripts/okr/coverage.py) MUST skip this file by matching STUB_MARKER. -->
	<sources>
		<source>apps/web</source>
	</sources>
	<packages>
		<package name="${STUB_MARKER}" line-rate="0" branch-rate="0" complexity="0">
			<classes/>
		</package>
	</packages>
</coverage>
`

/**
 * Return the stub XML as a string. Pure function — no I/O.
 *
 * Exposed for testing. Production runs use `emitCoverageStub()` to write the
 * file at the canonical apps/web/coverage.xml location.
 */
export function buildStubCoverageXml() {
  // Trailing newline so POSIX tools and linters stay happy.
  return STUB_BODY.endsWith("\n") ? STUB_BODY : STUB_BODY + "\n"
}

/**
 * Default output location: apps/web/coverage.xml, relative to the
 * monorepo root. Override via the first CLI argument for tests.
 */
const DEFAULT_OUTPUT = resolve(__dirname, "..", "coverage.xml")

export function emitCoverageStub(outputPath = DEFAULT_OUTPUT) {
  mkdirSync(dirname(outputPath), { recursive: true })
  writeFileSync(outputPath, buildStubCoverageXml(), "utf8")
  return outputPath
}

// Run when invoked directly (`node scripts/emit-coverage-stub.mjs`).
// Guarded so importing the module in tests does not trigger I/O.
const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (invokedDirectly) {
  const output = emitCoverageStub()
  process.stdout.write(`wrote ${output}\n`)
}
