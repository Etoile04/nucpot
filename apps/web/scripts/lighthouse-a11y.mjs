#!/usr/bin/env node
/**
 * Lighthouse a11y audit runner — NFM-3794.
 *
 * Audits the 3 ontology management pages served by `pnpm start` and writes
 * JSON + HTML reports under apps/web/qa-artifacts/lighthouse/<timestamp>/.
 *
 * Usage:
 *   node scripts/lighthouse-a11y.mjs                                  # default http://localhost:3000
 *   BASE_URL=http://localhost:3300 node scripts/lighthouse-a11y.mjs
 *   ROUTES="/admin/ontology,/admin/ontology/foo" node scripts/lighthouse-a11y.mjs
 *   MIN_SCORE=90 node scripts/lighthouse-a11y.mjs
 *
 * Exit code 0 if every page scores >= MIN_SCORE (default 90), else 1.
 */
import lighthouse from 'lighthouse'
import * as chromeLauncher from 'chrome-launcher'
import fs from 'node:fs/promises'
import path from 'node:path'

const BASE_URL = process.env.BASE_URL ?? 'http://localhost:3000'
const MIN_SCORE = Number.parseInt(process.env.MIN_SCORE ?? '90', 10)
const DEFAULT_ROUTES = [
  '/admin/ontology',
  '/admin/ontology/sample-type-id',
  '/admin/ontology/sample-type-id/edit',
]
const ROUTES = (process.env.ROUTES ?? DEFAULT_ROUTES.join(',')).split(',')
const OUT_ROOT = path.resolve(
  'qa-artifacts/lighthouse',
  new Date().toISOString().replace(/[:.]/g, '-'),
)
// NFM-1039: only default to the macOS app-bundle path on darwin. On Linux CI
// (and any platform without it) fall through to chrome-launcher's own
// discovery, or an explicit CHROME_PATH env when provided.
const CHROME_PATH =
  process.env.CHROME_PATH ??
  (process.platform === 'darwin'
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    : undefined)

function summarizeAudits(lhr) {
  const failing = []
  const manual = []
  for (const auditRef of lhr.categories.accessibility.auditRefs) {
    const a = lhr.audits[auditRef.id]
    if (!a) continue
    if (a.score === null || a.score === undefined) {
      manual.push({ id: auditRef.id, title: a.title })
      continue
    }
    if (a.score < 1) {
      failing.push({
        id: auditRef.id,
        title: a.title,
        score: a.score,
        displayValue: a.displayValue,
        details: a.details,
      })
    }
  }
  return { failing, manual }
}

async function auditRoute(url) {
  // Fresh Chrome per route — reusing a single instance across pages can
  // surface chrome-error://chromewebdata/ interstitials when navigation
  // races with prior-page teardown (observed in NFM-3794).
  const chrome = await chromeLauncher.launch({
    // NFM-1039: pass chromePath only when we actually resolved one — an
    // explicit CHROME_PATH env or the darwin default. Otherwise let
    // chrome-launcher discover Chrome/Chromium itself (Linux CI uses the
    // Playwright-installed chromium exported by the workflow).
    ...(CHROME_PATH ? { chromePath: CHROME_PATH } : {}),
    chromeFlags: [
      '--headless=new',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  })
  try {
    const opts = {
      logLevel: 'error',
      output: ['json', 'html'],
      onlyCategories: ['accessibility'],
      port: chrome.port,
      formFactor: 'desktop',
      screenEmulation: {
        mobile: false,
        width: 1440,
        height: 900,
        deviceScaleFactor: 1,
        disabled: false,
      },
      throttlingMethod: 'provided',
    }
    const runnerResult = await lighthouse(url, opts)
    if (!runnerResult) throw new Error(`Lighthouse returned no result for ${url}`)
    const reports = Array.isArray(runnerResult.report) ? runnerResult.report : [runnerResult.report]
    return { lhr: runnerResult.lhr, htmlReport: reports[1] ?? reports[0] }
  } finally {
    await chrome.kill()
  }
}

async function main() {
  await fs.mkdir(OUT_ROOT, { recursive: true })

  const summary = []
  let allPassed = true

  for (const route of ROUTES) {
    const url = BASE_URL.replace(/\/$/, '') + route
    const slug = route.replace(/^\//, '').replace(/\//g, '__') || 'root'
    process.stderr.write(`→ auditing ${url}\n`)

    let lhr, htmlReport
    try {
      ({ lhr, htmlReport } = await auditRoute(url))
    } catch (err) {
      process.stderr.write(`  ✗ audit failed: ${err.message}\n`)
      summary.push({ route, url, score: 0, pass: false, error: err.message, failing: [], manualChecks: [], report: null })
      allPassed = false
      continue
    }

    const score = Math.round((lhr.categories.accessibility.score ?? 0) * 100)
    const { failing, manual } = summarizeAudits(lhr)

    const jsonPath = path.join(OUT_ROOT, `${slug}.json`)
    const htmlPath = path.join(OUT_ROOT, `${slug}.html`)
    await fs.writeFile(jsonPath, JSON.stringify(lhr, null, 2))
    await fs.writeFile(htmlPath, htmlReport)

    const ok = score >= MIN_SCORE
    if (!ok) allPassed = false

    summary.push({
      route,
      url,
      score,
      pass: ok,
      failing: failing.map((f) => ({ id: f.id, title: f.title, score: f.score })),
      manualChecks: manual.map((m) => m.id),
      report: { json: jsonPath, html: htmlPath },
    })

    process.stderr.write(
      `  score=${score} pass=${ok} failing=${failing.length} manual=${manual.length}\n`,
    )
    for (const f of failing) {
      process.stderr.write(`    ✗ ${f.id} (${f.title}) — score=${f.score}\n`)
    }
  }

  const summaryPath = path.join(OUT_ROOT, 'summary.json')
  await fs.writeFile(
    summaryPath,
    JSON.stringify({ baseUrl: BASE_URL, minScore: MIN_SCORE, results: summary }, null, 2),
  )

  const md = [
    `# Lighthouse a11y audit — ${new Date().toISOString()}`,
    '',
    `- Base URL: \`${BASE_URL}\``,
    `- Min score: **${MIN_SCORE}**`,
    `- Report dir: \`${OUT_ROOT}\``,
    '',
    '| Route | Score | Pass | Failing audits |',
    '| --- | --- | --- | --- |',
    ...summary.map((s) => {
      const failing = s.failing.map((f) => `\`${f.id}\``).join(', ') || '—'
      return `| \`${s.route}\` | ${s.score} | ${s.pass ? '✅' : '❌'} | ${failing} |`
    }),
    '',
  ].join('\n')
  await fs.writeFile(path.join(OUT_ROOT, 'summary.md'), md)

  console.log(
    JSON.stringify({ summaryPath, outRoot: OUT_ROOT, results: summary }, null, 2),
  )

  if (!allPassed) {
    process.exitCode = 1
  }
}

main().catch(async (err) => {
  console.error(err)
  // NFM-1039: even on a top-level crash, write a summary.md so the artifact
  // upload step (if-no-files-found: error) never turns a script crash into
  // a cascade of unrelated red steps.
  try {
    await fs.mkdir(OUT_ROOT, { recursive: true })
    await fs.writeFile(
      path.join(OUT_ROOT, 'summary.md'),
      `# Lighthouse a11y audit — crashed\n\n\`\`\`\n${String(err?.stack ?? err)}\n\`\`\`\n`,
    )
  } catch {
    /* nothing more we can do */
  }
  process.exit(2)
})
