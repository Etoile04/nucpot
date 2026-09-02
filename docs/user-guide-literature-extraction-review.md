# Literature Extraction & Human Review — User Guide

> 版本: 2026-07-27 · Author: NucPot Engineering · Audit Status: E2E Verified on Production

## Overview

The Literature Extraction & Human Review workflow turns a research paper PDF (or any on-disk text) into structured nuclear-material property data that flows through a **staging → review → approval** pipeline. The LLM (Qwen3.6:35b-a3b-coding-nvfp4, served via Ollama) extracts candidate properties, the quality gate deduplicates and range-validates them, and a human reviewer (you) decides which ones stay in the knowledge graph.

This guide walks through the four core flows a customer will use:

1. Uploading a literature source
2. Triggering extraction
3. Reviewing / approving extracted properties
4. Browsing the knowledge graph and source traceability

---

## Prerequisites

- **Production URL**: `https://nucpot.dpdns.org`
- **Login credentials** (provided by your admin; the test account is `lwj280@gmail.com` / `demo1234`)
- A modern browser (Chrome / Edge / Safari — the UI has been validated on these)
- The first extraction of a document takes **30–120 seconds**; subsequent runs are faster

> ⚠️ **Time expectation**: Qwen3.6 uses a *thinking* mode that generates 3,000–8,000 tokens of reasoning before producing its final JSON answer. On the Mac Studio DevOps host, a 57 KB review paper takes ~60 seconds end-to-end. A 281 KB PNNL FRAPCON report may take 90+ seconds.

---

## 1. Login

### 1.1 Navigate to the login page

Open `https://nucpot.dpdns.org/login` in your browser. You'll see:

![login page — email + password form](docs/screenshots/login.png)

The form has two fields:

| Field | Value |
|-------|-------|
| 邮箱 (Email) | your registered email |
| 密码 (Password) | your password |

### 1.2 Submit the form

Click **登录 (Sign In)**. The browser stores your JWT in an `HttpOnly` cookie called `access_token`. The cookie is auto-sent with every subsequent request — no further login is needed during your session.

> **Known UI issue** *(to be fixed in NFM-1902)*: The `/login` React form does not yet wire its `onSubmit` handler. Until that ships, sign in via `https://nucpot.dpdns.org/admin/login?redirect=%2Freview%2Fkg` instead — that admin form does submit correctly.

### 1.3 Verify you're logged in

After successful login the URL bar shows a redirect to `/dashboard` (or your target page). You can also confirm by visiting `https://nucpot.dpdns.org/api/v1/auth/me` — a `200 OK` proves the cookie is valid.

---

## 2. Browse Materials and Search

### 2.1 Materials library

Click **材料库 (Materials Library)** in the top navigation, or visit `https://nucpot.dpdns.org/potentials`.

The page lists every registered material with:

- Element system badges (U, O, Cr, etc.)
- Source attribution (PNNL, FRAPCON, Journal of Nuclear Materials)
- Review status: ✅ approved · 🟡 pending · 🔴 rejected · 🔵 needs_revision

You can filter by element, status, or free-text search.

![materials library — list view](docs/screenshots/materials-library.png)

### 2.2 Advanced search

`/advanced-search` lets you combine filters:

- **Element**: pick one or many (U, U-Pu, U-Cr, …)
- **Property**: activation energy, thermal conductivity K1, density, …
- **Temperature range** in °C
- **Source**: filter by paper, DOI, or PNNL report

A canonical query — *“Cr-doped UO₂, activation energy, 200–600 °C”* — returns properties from the Owen et al. 2023 study along with the Frankel-cluster reframes from PNNL-19585.

---

## 3. Trigger Literature Extraction

### 3.1 Open the Literature Management page

Visit `https://nucpot.dpdns.org/literature`. You'll see a list of all uploaded sources:

![literature table with parse_status column](docs/screenshots/literature-list.png)

The status indicators are:

| `parse_status` | Meaning |
|----------------|---------|
| `uploaded` | PDF/MD file present, not yet extracted |
| `completed` | Extraction ran; rows in `_ref_gap_fill_staging` |
| `extracting` | Pipeline currently in progress |
| `failed` | Pipeline aborted (check logs) |

### 3.2 Add a new source

There are two ways:

- **Drag & drop**: drag a PDF onto the upload area; the UI calls `/api/v1/literature/upload` and parses it.
- **By DOI**: in the sidebar, paste a DOI like `10.1016/j.jnucmat.2023.154270`; the backend downloads the PDF and parses it.

The text content (Markdown) is stored in `data_sources.content_md`.

### 3.3 Trigger extraction

Click the row's **提取 (Extract)** button. Behind the scenes the UI calls:

```http
POST /api/v1/extraction/trigger
Content-Type: application/json
Authorization: Bearer <your token>

{
  "source_reference": "9320cb50-eb65-4178-8d2e-c56aeb848b21",
  "source_type": "datasource"
}
```

The button immediately turns into a spinner, and within ~50 ms the API returns:

```json
{
  "success": true,
  "data": {
    "source_reference": "9320cb50-...",
    "source_type": "datasource",
    "status": "queued",
    "message": "Extraction job queued. Check review queue for results."
  }
}
```

![extraction queued — toast notification](docs/screenshots/extraction-queued.png)

> **Why is it so fast?** Until 2026-07-27, the API would block for 30–120 seconds waiting for the LLM, and Cloudflare Tunnel would time it out at 100 s. We now fire-and-forget the LLM call in a background task, so the API returns `queued` immediately and the extraction runs server-side without holding the HTTP connection open.

### 3.4 Watch progress

Refresh the literature page after 60–90 seconds. The source's `parse_status` flips to `completed` and the **Review Queue** page gets new rows.

You can also poll directly:

```bash
curl https://nucpot.dpdns.org/api/v1/review/pending
```

---

## 4. Human Review

### 4.1 The Review Queue

Visit `https://nucpot.dpdns.org/review/pending`. You'll see a paginated table of all extracted candidate properties:

![review queue — list view](docs/screenshots/review-queue.png)

Each row shows:

| Column | Description |
|--------|-------------|
| Property name | `氧扩散激活能`, `热导率 K1`, … |
| Value | 0.3 eV, 296.7 J/kg·K, … (parsed from LLM JSON) |
| Confidence | `high` / `medium` / `low` (assigned by quality gate) |
| Status | `pending`, `needs_revision`, etc. |
| Source | paper title + DOI link |

### 4.2 Inspect a single property

Click any row to open the detail panel. The layout is:

```
┌─────────────────────────────┬─────────────────────────────┐
│  Source text (left)         │  Property card (right)      │
│  ──────────                 │  ─────────                  │
│  FRAPCON-4.0 Table 2.1      │  unit:  J/kg·K              │
│  Thermal conductivity…      │  value: 296.7               │
│  K1 coefficient (Ronchi…)   │  desc:  K1 in Ronchi fit    │
└─────────────────────────────┴─────────────────────────────┘
```

![review detail with source panel](docs/screenshots/review-detail.png)

The left pane shows the matching paragraph(s) from the source document; the right pane shows the structured property card. This is the **traceability** backbone — every value traces back to exact text.

### 4.3 Approve / Reject / Needs-revision

In the right panel click one of:

- **✓ 批准 (Approve)**: writes the property to `kg_nodes` with `review_status = approved` and adds an audit row.
- **✗ 驳回 (Reject)**: requires a `reason` field. Writes `rejected` status with the reason captured.
- **↻ 需要修订 (Needs Revision)**: writes `needs_revision` so the agent or your colleague can correct the value.

Each click instantly refreshes the row counter at the bottom of the queue. A green toast appears:

![review action toast — Approved](docs/screenshots/review-action.png)

### 4.4 Bulk operations

Select 5+ rows with the checkboxes, then choose **批量批准 (Bulk Approve)** or **批量驳回 (Bulk Reject)** from the toolbar. A confirmation dialog summarises the count.

---

## 5. Knowledge Graph Browsing

### 5.1 Open the KG explorer

Visit `https://nucpot.dpdns.org/kg`. The explorer shows materials, properties, experiments, and conditions as nodes connected by edges:

![knowledge graph — overview](docs/screenshots/kg-explorer.png)

**Mouse interactions**:

- **Scroll-zoom**: hover the canvas and roll the wheel
- **Pan**: click-and-drag empty space
- **Click node**: open the property's review detail panel
- **Double-click node**: focus and re-centre the canvas

### 5.2 Search and filter

Type in the search bar (top-left). The graph re-renders to show only matched nodes and their 1-hop neighbours. Useful queries:

- `UO2` → material node + all attached properties
- `activation_energy` → property node + experiments that measured it
- `FRAPCON` → paper nodes + downstream properties

### 5.3 Source provenance

Click any **property** node → click **溯源 (Trace Source)** in the toolbar. The drawer shows:

- The source document's title, DOI, and parse status
- The matching source paragraphs (highlighted)
- The LLM extraction prompt that produced this value
- The quality-gate decision (dedup hash, range-valid result)

![source provenance panel](docs/screenshots/source-provenance.png)

---

## 6. Conflicts & Adjudication

The system cross-checks every approved property against existing entries. When two sources disagree, a *conflict record* is created.

### 6.1 Find conflicts

Visit `https://nucpot.dpdns.org/review/conflicts`:

![conflict table](docs/screenshots/conflicts.png)

Columns: property, source A value, source B value, source A title, source B title.

### 6.2 Adjudicate

Click a row → choose which value wins:

- **接受源 A / 接受源 B (Adopt A / Adopt B)**: marks the loser's value as `rejected` with reason "superseded"
- **手动校正 (Manual Override)**: type a new value, save, and both rows update to point to your override as the canonical source

---

## 7. Statistics & Audit Trail

Visit `https://nucpot.dpdns.org/review/stats`. Behind the scenes, this calls:

```bash
curl https://nucpot.dpdns.org/api/v1/review/stats
```

Returns:

```json
{
  "success": true,
  "data": {
    "pending": 9,
    "pending_review": 0,
    "approved": 3,
    "rejected": 0,
    "needs_revision": 2,
    "total_reviewed": 5,
    "adoption_rate": null,
    "by_type": {
      "extraction": { "total": 0, "corrected": 0, "rejected": 0, "approved": 0, "adoption_rate": null },
      "node":      { "total": 3, "corrected": 0, "rejected": 0, "approved": 3, "adoption_rate": null },
      "edge":      { "total": 0, "corrected": 0, "rejected": 0, "approved": 0, "adoption_rate": null },
      "measurement": { "total": 0, "corrected": 0, "rejected": 0, "approved": 0, "adoption_rate": null }
    }
  }
}
```

Field meanings:

- `adoption_rate` = `corrected / (corrected + rejected)` — your fraction of LLM suggestions accepted
- `by_type` breaks down approvals per content class

![review stats dashboard](docs/screenshots/review-stats.png)

---

## 8. End-to-End Demo Scenario

A complete walkthrough — useful for both first-time users and regression testing.

### 8.1 Goal

Extract properties from the Owen et al. 2023 Cr-doped UO₂ paper, approve them, and check the KG reflects the new nodes.

### 8.2 Steps

1. **Login** to `https://nucpot.dpdns.org/admin/login`
2. **Literature page** → find `Owen et al. - 2023 - Diffusion in undoped and Cr-doped amorphous UO2` (status: `extracting`)
3. **Click 提取 (Extract)** — toast shows `queued`
4. **Wait 90 seconds**, then refresh — status becomes `completed`
5. **Open /review/pending** — look for `activation_energy`, value `0.3 eV`, with source label `Owen et al. 2023`
6. **Click the row** to open the detail panel; verify the source paragraph matches Table 3 of the paper
7. **Click ✓ 批准 (Approve)** — row disappears from queue, appears in `/kg` graph
8. **Visit /kg**, search `UO2` — confirm the new `Property → activation_energy (0.3 eV)` node is connected to the `UO2` material node
9. **Visit /review/stats** — `approved` count incremented by 1

### 8.3 Expected outcome

```
kg_nodes count:        +1 (activation_energy, 0.3 eV, approved)
_ref_gap_fill_staging: +1 row (扩散激活能, 0.30 ± 0.05)
review/stats.approved: 3 → 4
```

---

## 9. Troubleshooting

### 9.1 Extraction stays in `queued` status

**Symptom**: After 5 minutes, the source row still says `queued`.

**Fix**:

```bash
docker exec nucpot-prod-api ls /tmp/healthcheck.log 2>&1
docker logs nucpot-prod-api --tail 100 | grep -i "extraction\|llm"
```

If you see `LLM request error` or `connect: connection refused` to `192.168.3.200:11434`, Ollama is down. Restart it:

```bash
brew services restart ollama
# or:
ollama serve &
```

### 9.2 Extraction returns 502 from external URL

**Symptom**: Cloudflare Tunnel returns 502 before the extraction completes.

**Status**: This was observed on 2026-07-25 and root-cause-fixed on 2026-07-27 by making the trigger asynchronous. If you see it again:

1. The trigger may have been in flight when the fix deployed (rollback race)
2. Check that `nucpot-prod-api` is running the latest image (`docker inspect --format '{{.Image}}' nucpot-prod-api`)

### 9.3 Login form does nothing when I click 登录

**Symptom**: The blue 登录 button clicks but no network request fires.

**Status (2026-07-27 PM)**: ✅ Verified working in production.  Tests on `https://nucpot.dpdns.org/login` show that the form correctly POSTs to `/api/v1/auth/login` and sets the `access_token` HttpOnly cookie. The earlier "no network request" reports were caused by manually injecting invalid JWTs via `document.cookie`, which made `/api/v1/auth/me` return 401 indefinitely.

**If you still see this**:
1. Open DevTools → Network panel
2. Click 登录
3. Look for `POST /api/v1/auth/login` (must return 200)
4. If missing, check that JavaScript is enabled and the page is fully loaded (try Cmd-R)
5. If the POST returns 401, double-check your credentials with your administrator

**Long-term fixes shipped**: NFM-1004 (ReviewAuthGuard now uses `window.location.replace` as a safety net so `/review/kg` always redirects rather than spinning forever).

### 9.4 Property value shown as `0.0`

**Symptom**: Quality gate fell back to `0.0` because the LLM returned a non-numeric string (e.g. `3.32 × 10^-8`).

**Fix**: Implemented on 2026-07-27 with the `_safe_float()` helper that recognises `±` uncertainty notation and `×10^N` scientific notation. If you still see this, paste the raw value into `/review` and approve with an override.

---

## 10. API Quick Reference

All endpoints under `/api/v1`. Replace `<TOKEN>` with the JWT cookie (set automatically by the browser).

```bash
# Health check
GET /api/v1/health

# Login (form-encoded)
POST /api/v1/auth/login
  username=<email>&password=<password>
  → 200 {access_token, token_type: bearer}

# Trigger extraction (returns immediately)
POST /api/v1/extraction/trigger
  {"source_reference": "<uuid>", "source_type": "datasource"}
  → 202 {status: "queued", ...}

# Pending review items
GET /api/v1/review/pending?status=pending&limit=20&offset=0

# Approve
POST /api/v1/review/{node_id}/approve
  {"reviewer_notes": "verified against Table 3"}

# Reject (REQUIRES 'reason' field, not 'reviewer_notes')
POST /api/v1/review/{node_id}/reject
  {"reason": "value out of range for Cr-doped UO₂"}

# Stats
GET /api/v1/review/stats

# KG search
GET /api/v1/kg/nodes?label=UO2&status=approved

# Source provenance
GET /api/v1/review/{node_id}/source
```

For full OpenAPI docs visit `https://nucpot.dpdns.org/api/docs` (Swagger UI).

---

## Appendix A — Test Data Available Now

| Source ID | Title | Status |
|-----------|-------|--------|
| `9320cb50-eb65-4178-8d2e-c56aeb848b21` | Owen et al. 2023, Cr-doped amorphous UO₂ diffusion (57 KB) | completed |
| `1a0f45d9-b4a4-45f2-a073-9d5779139e9c` | PNNL FRAPCON-4.0 vs FRAPTRAN-2.0 vs MATPRO (281 KB) | completed |
| `7d48ea92-4d6c-4f37-89cc-33394cdd0b46` | Material Property Correlations (short abstract) | completed |
| `a4c37a11-13da-4316-8025-7adb1b9c5651` | Terricabras et al. 2025, Cr doping grain-boundary chemistry | completed |

Use any of these UUIDs in the extraction/trigger call to verify end-to-end behaviour without uploading your own paper.

## Appendix B — Glossary

| Term | Meaning |
|------|---------|
| **DataSource** | A registered literature entry (PDF + parsed Markdown) |
| **kg_node** | A node in the knowledge graph (Material, Property, Condition, Experiment, …) |
| **kg_edge** | A typed relationship between two nodes |
| **Staging row** | A pre-approval candidate in `_ref_gap_fill_staging` |
| **Quality gate** | Dedup + range validation + confidence routing |
| **Review status** | `pending` (KG) or `pending_review` (extraction) → `approved` / `rejected` / `needs_revision` / `corrected` |
| **Adoption rate** | `corrected / (corrected + rejected)` — your trust calibration of the LLM |

---

*Questions? Email `nucpot@agentmail.to` or open a Paperclip issue under the Knowledge Management project.*
