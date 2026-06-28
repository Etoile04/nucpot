# NFM-22: Literature Search & Download Technical Gap Analysis

**Date:** 2026-06-09
**Author:** CTO
**Status:** Final — research integrated

## 1. Problem Statement

The NFMDP's data gap identification workflow requires:

1. **Discovery** — systematically searching academic literature for nuclear fuel and materials property data
2. **Acquisition** — downloading full-text papers from behind paywalls and anti-bot protections
3. **Extraction** — pulling structured property measurements from papers (tables, figures, text)
4. **Ingestion** — inserting extracted data into the NFM platform with full provenance

The critical bottleneck is **phase 1–2**: major academic databases (ScienceDirect, CNKI, Springer, Wiley) employ anti-crawler techniques that block automated access. This analysis evaluates tools and architectures to build an automated literature pipeline.

## 2. Target Data Sources

| Source | Coverage | Access Barrier | Content Type | Priority |
|--------|----------|---------------|-------------|----------|
| **ScienceDirect** (Elsevier) | Major nuclear materials journals | Paywall + anti-bot | Full-text PDF, HTML | Critical |
| **CNKI** (中国知网) | Chinese-language nuclear research | Paywall + IP restriction + anti-bot | PDF (CAJ format) | Critical |
| **Springer/Nature** | Nuclear engineering, materials science | Paywall + API (Springer Nature API) | Full-text PDF | High |
| **Wiley** | Nuclear materials, corrosion science | Paywall + anti-bot | Full-text PDF | High |
| **Google Scholar** | Cross-publisher discovery | Rate limiting + CAPTCHA | Metadata + links | High |
| **Semantic Scholar** | Open metadata, some full-text | Free API (rate-limited) | Metadata, abstracts, citations | Medium |
| **CrossRef** | DOI metadata, licensing info | Free API | Metadata only | Medium |
| **OpenAlex** | Open scholarly metadata | Free API | Metadata, citations | Medium |
| **arXiv** | Preprints, some nuclear physics | Free API | Full-text PDF | Low |
| **CORE** | Open access aggregator | Free API + premium | Full-text (OA only) | Low |

## 3. Tool Evaluation

### 3.1 BrightData MCP

**Repo:** [brightdata/brightdata-mcp](https://github.com/brightdata/brightdata-mcp) (2,400+ stars, 311 forks)
**Installation:** `npx @brightdata/mcp`

| Aspect | Details |
|--------|---------|
| **Anti-bot bypass** | 99.3%+ success rate; handles Cloudflare, Akamai, PerimeterX, DataDome |
| **CAPTCHA solving** | Automatic reCAPTCHA, hCaptcha, Cloudflare Turnstile (20+ types) |
| **Proxy pool** | 400M+ residential IPs across 195+ countries |
| **TLS fingerprinting** | Generates unique TLS fingerprints per request |
| **Browser automation** | Full Chrome DevTools Protocol control for complex interactions |

**Core tools (Free Tier — 5,000 requests/month):**
- `search_engine` — Google/Bing/Yandex search
- `scrape_as_markdown` — Extract pages as clean Markdown
- `discover` — AI-ranked search with relevance scoring

**Advanced tools (Pro Mode):**
- `scraping_browser_navigate` / `click_ref` / `get_text` — Full browser control
- `extract` — AI-powered structured data extraction
- `scrape_batch` / `search_engine_batch` — Parallel operations

**Pricing:**
- Free: 5,000 requests/month (sufficient for pilot)
- Pay-as-you-go: $1.1–1.5 per 1,000 results, $5–8 per GB
- Academic: "The Bright Initiative" offers pro-bono access for research institutions (up to $20K)
- Monthly plans: $499 / $999 / $1,999

**Feasibility for NFMDP:** ✅ Highly feasible. Free tier sufficient for proof-of-concept. Academic partnership could eliminate cost for research use.

### 3.2 Zotero MCP

**Primary implementation:** [54yyyu/zotero-mcp](https://github.com/54yyyu/zotero-mcp) (2,865 stars, Python)
**Alternatives:** [cookjohn/zotero-mcp](https://github.com/cookjohn/zotero-mcp) (617 stars, TypeScript plugin), [drxaibi/zotero-mcp](https://github.com/drxaibi/zotero-mcp) (dual Web API/SQLite backend)

| Aspect | Details |
|--------|---------|
| **Search** | Hybrid keyword + vector search (bge-m3 embeddings), full-text across PDFs |
| **CNKI support** | Community translators (translators_CN), jasminum plugin for CNKI metadata |
| **PDF download** | Unpaywall integration (automatic OA PDF discovery), custom resolvers |
| **Export formats** | BibTeX, RIS, CSL-JSON, TEI, RDF, MODS, CSV |
| **Citation network** | Forward/backward citation analysis, related work discovery |
| **Multi-source discovery** | Parallel queries to OpenAlex, CrossRef, Semantic Scholar |

**API capabilities:**
- Local API (localhost:23119): No auth, no rate limits, full access to local SQLite
- Web API: API key or OAuth 1.0a, rate limit via `Backoff` header, max 4 concurrent requests
- Write operations: Create/update items, collections, notes, file uploads

**Chinese-focused options:**
- `@xbghc/zotero-mcp` — Chinese-focused with DOI/ISBN/PMID support
- `qiobn/zotero-research-mcp` — CNKI-specific MCP server
- PaddleOCR integration for Chinese PDF text extraction

**Feasibility for NFMDP:** ✅ Excellent fit. Mature ecosystem, Chinese language support, proven MCP integrations. Best choice for reference management and PDF organization layer.

### 3.3 Elsevier / ScienceDirect Access

**Official API:** [Elsevier Developer Portal](https://dev.elsevier.com)
- ScienceDirect Journals API: Full-text access for subscribed institutions
- Requires institutional API key + active subscription
- Rate limits: 10 req/sec, 500 req/week
- Returns XML format

**MCP server:** [elsevier-mcp](https://github.com/kemalabuteliyte/elsevier-mcp)
- 6 free tools (Scopus search, abstract retrieval, author search)
- 8 premium tools (full-text retrieval, requires institutional access)
- Direct Claude Code integration

**Community tool:** [paper-scraper](https://github.com/GAO-pooh/paper-scraper)
- Chrome DevTools Protocol approach for PDF capture
- Bypasses Cloudflare via browser automation
- Requires institutional SSO/CARSI access
- macOS only (hardcoded Chrome paths)

**Anti-crawler defenses (ScienceDirect):**
- Cloudflare bot detection
- TLS fingerprinting
- Rate limiting (IP-based)
- Session validation

**Mitigation strategy:** API-first (Elsevier API with institutional key) → BrightData scraping as fallback → Browser automation for edge cases.

### 3.4 CNKI (中国知网) Access

**MCP server:** [cnki-mcp-server](https://pypi.org/project/cnki-mcp-server/)
- Playwright-based browser automation
- 15 search types, 17 metadata fields
- Anti-detection: Random User-Agent, human input simulation

**CLI tool:** [cnki-search](https://github.com/ExquisiteCore/cnki-search)
- Go-based, direct HTTP access to `kns.cnki.net`
- Exit codes: 0=success, 2=CAPTCHA/anti-bot, 3=no results
- Multi-field search: topic/keyword/title/author/abstract/DOI

**Access barriers (2023+ restrictions):**
- Foreign access limited: Dissertations, conference proceedings, statistical yearbooks restricted
- Still accessible: China Academic Journals (70M+ articles)
- Tencent EdgeOne CDN, 418 status codes for proxy/server IPs
- Monopoly pricing: CNKI controls 95% of Chinese academic literature

**Feasibility for NFMDP:** ⚠️ Feasible but legally sensitive. The cnki-mcp-server provides technical capability, but CNKI ToS may prohibit automated access. Legal review required before deployment.

### 3.5 Google Scholar Integration

**Commercial API:** SerpApi Google Scholar API
- Reliable structured JSON output, handles CAPTCHAs automatically
- Parameters: query, year range, citation search, author filter
- Paid subscription required for production use

**Free library:** `scholarly` (Python, unofficial)
- Free but frequently blocked after 20–40 requests
- Requires proxy rotation for sustained use
- Suitable for prototyping only

**Anti-bot limitations:**
- No official API available
- CAPTCHA challenges after sustained requests
- IP-based throttling and temporary bans
- Pagination limited to ~100 results maximum

**Recommended approach:** SerpApi for production, `scholarly` for prototyping. Google Scholar is best used for discovery (metadata), not full-text access.

### 3.6 Open Access APIs (No Authentication Required)

| API | Coverage | Rate Limits | Best For |
|-----|----------|-------------|----------|
| **OpenAlex** | 270M+ works | Polite requests (email) | Broad discovery, citation graphs |
| **Semantic Scholar** | 200M+ papers | 1 req/sec (with key) | Relevance-ranked results, TLDR summaries |
| **CrossRef** | 140M+ DOIs | 50 req/sec (registered) | DOI metadata, licensing info |
| **Unpaywall** | OA PDF locator | None (email) | Finding free PDF versions |
| **arXiv** | 2.4M preprints | 1 req/sec | Physics/nuclear preprints |
| **PubMed** | 36M citations | 3–10 req/sec | Biomedical nuclear research |

### 3.7 Existing Literature Pipeline Tools

**litscout** ([PyPI](https://pypi.org/project/litscout/)):
- AI-powered literature discovery and screening
- Multi-source search (OpenAlex, Semantic Scholar, arXiv, PubMed, CORE)
- LLM relevance filtering with configurable criteria
- Automated PDF retrieval with Elsevier fallback
- Markdown report generation

**article-downloader** ([GitHub](https://github.com/sShuaiYang/article-downloader)):
- Publisher-approved TDM APIs (Elsevier, Wiley, Springer, CrossRef)
- Requires individual API keys per publisher
- Best for large-scale text mining with proper permissions

**Feasibility for NFMDP:** ✅ `litscout` is a strong candidate for the discovery/screening stage. Can be extended with domain-specific relevance criteria for nuclear materials.

## 4. Technical Gap Analysis

### 4.1 Gap: Cross-Source Search Discovery

**Current state:** Manual keyword searches across individual databases.

**Gap:** No unified search that covers ScienceDirect, CNKI, Google Scholar, and open APIs simultaneously.

**Required capability:**
- Unified query interface accepting materials/property terms
- Parallel search across multiple sources
- Deduplication of results across sources (same paper on multiple platforms)
- Citation graph traversal to find related work
- Filter by: material system, property type, temperature/pressure range, irradiation conditions

**Candidate solution:** Agentic search pipeline using Google Scholar (discovery) → Semantic Scholar/CrossRef (metadata enrichment) → Source-specific downloaders (acquisition).

### 4.2 Gap: Authenticated Full-Text Download

**Current state:** Manual browser-based download using institutional credentials.

**Gap:** Automated download blocked by CAPTCHAs, rate limits, session management, paywalls.

**Required capability:**
- Authenticated session management (institutional SSO / VPN)
- CAPTCHA handling or avoidance
- PDF download with retry and resume
- Multiple source fallback (same paper from different hosts)
- Legal/licensing compliance (fair use, institutional subscription terms)

**Candidate approach:**
- **Legitimate path:** Use institutional API keys where available (Elsevier API, Springer API)
- **Scraping path:** BrightData for sources without API access
- **Hybrid:** API-first, scraping as fallback for sources without programmatic access

### 4.3 Gap: Chinese-Language Source Access (CNKI)

**Current state:** Manual download from CNKI web interface.

**Gap:** CNKI uses proprietary CAJ format, IP-based access control, and aggressive anti-crawler.

**Required capability:**
- CNKI-specific scraper with authenticated session
- CAJ → PDF conversion
- Chinese-language metadata extraction and normalization
- Cross-referencing with English-language papers on same topics

**Risk:** CNKI's terms of service may explicitly prohibit automated access. Need legal review.

### 4.4 Gap: PDF-to-Structured-Data Extraction

**Current state:** Manual reading and transcription of tables and figures.

**Gap:** No automated pipeline for extracting property measurements from PDFs.

**Required capability:**
- Table detection and extraction from PDFs
- Figure/chart data extraction (plot digitization)
- Multi-column layout parsing (common in academic papers)
- Chinese + English text OCR
- Unit detection and normalization
- Material composition parsing
- Measurement condition extraction

**Candidate tools:**
- **PDF parsing:** MinerU MCP (already available in our toolchain), PyMuPDF, GROBID
- **Table extraction:** Camelot, Tabula, pdfplumber
- **Plot digitization:** WebPlotDigitizer, plotdigitizer Python
- **LLM-assisted extraction:** Structured extraction prompts against property measurement schema
- **OCR:** Tesseract, PaddleOCR (Chinese support)

### 4.5 Gap: Provenance Tracking

**Current state:** Manual citation recording in spreadsheet entries.

**Gap:** No automated link from extracted data point back to source paper, page, table, row.

**Required capability:**
- Automatic DOI resolution
- Source paper metadata storage (authors, year, journal, volume, pages)
- Fine-grained provenance: paper → page → table → row → cell
- Version tracking (corrections, errata)
- Link to NFM `data_sources` and `authors` tables

**Integration with NFM data model:** The `data_sources`, `authors`, and `data_source_authors` tables in the NFM schema (NFM-4, rev 4) are designed for exactly this purpose. The pipeline needs to populate these tables automatically.

### 4.6 Gap: Agentic Workflow Integration

**Current state:** No automated workflow exists.

**Gap:** Need a multi-step agent pipeline that orchestrates search → download → extract → ingest.

**Required architecture:**
```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Search     │────▶│   Download   │────▶│   Extract    │────▶│   Ingest     │
│   Agent      │     │   Agent      │     │   Agent      │     │   Agent      │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                     │                    │
  Google Scholar       BrightData MCP         MinerU MCP          NFM API
  Semantic Scholar     Zotero MCP             GROBID              PostgreSQL
  CrossRef API         sd-skills              LLM extraction
  CNKI scraper         Institutional auth     PaddleOCR
```

**MCP integration pattern:** Each agent is a tool provider in the MCP protocol. The orchestrating agent calls tools from each specialist MCP server.

## 5. Proposed Architecture: Literature Acquisition Pipeline

### 5.1 Pipeline Stages

```
Stage 1: DISCOVERY
  Input:  Material name, property type, temperature range, etc.
  Tools:  Google Scholar (scholarly/SerpAPI), Semantic Scholar API, CrossRef API, OpenAlex
  Output: List of candidate papers with metadata (DOI, title, authors, year, abstract)

Stage 2: ACQUISITION
  Input:  Candidate paper list
  Tools:  Elsevier API (legitimate), Springer API, BrightData MCP (fallback), Zotero MCP
  Output: Downloaded PDFs with organized metadata

Stage 3: EXTRACTION
  Input:  PDFs + extraction schema (aligned to NFM property_measurements)
  Tools:  MinerU MCP, GROBID, Camelot, LLM-structured-extraction
  Output: Structured JSON matching NFM data model

Stage 4: INGESTION
  Input:  Extracted structured data
  Tools:  NFM Platform API (data_sources, authors, property_measurements)
  Output: Data in NFM database with full provenance

Stage 5: VERIFICATION
  Input:  Ingested data
  Tools:  nucpot-autovc verification pipeline, human review queue
  Output: Verified data ready for production
```

### 5.2 Technology Stack for Pipeline

| Component | Recommended Tool | Alternatives | Notes |
|-----------|-----------------|-------------|-------|
| Discovery search | `scholarly` + Semantic Scholar API | SerpAPI (paid), OpenAlex | Free tier limited; SerpAPI more reliable |
| Metadata enrichment | CrossRef API + OpenAlex | Semantic Scholar | Free, well-documented |
| PDF download (API) | Elsevier API key + Springer API | Institutional proxy | Requires API key registration |
| PDF download (scrape) | BrightData MCP | ScrapingBee, Oxylabs | Paid service, most reliable for anti-bot |
| Reference management | Zotero MCP | Mendeley API | Zotero has better MCP ecosystem |
| PDF parsing | MinerU MCP (already available) | PyMuPDF, GROBID | MinerU has Chinese text support |
| Table extraction | Camelot + pdfplumber | Tabula | Camelot best for complex tables |
| Plot digitization | `plotdigitizer` | WebPlotDigitizer | For extracting data from figures |
| OCR | PaddleOCR | Tesseract | PaddleOCR better for Chinese |
| Structured extraction | LLM (Claude/GPT) with schema | GROBID | LLM more flexible for domain extraction |
| Pipeline orchestration | Python + MCP client | LangChain, CrewAI | MCP-native preferred |

## 6. Risk Assessment

### 6.1 Legal and Licensing Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Terms of Service violation (web scraping) | **High** | Prefer official APIs; scraping only for open-access or with institutional credentials |
| Copyright infringement (full-text storage) | **High** | Store metadata + provenance; link to original; cache PDFs temporarily only |
| CNKI specific ToS | **High** | Legal review before any automated CNKI access |
| Data licensing (extracted values) | **Medium** | Property values are generally not copyrightable; cite sources properly |

### 6.2 Technical Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| BrightData cost escalation | **Medium** | Budget monitoring; free API sources first |
| Anti-crawler escalation | **Medium** | Fallback chains; rate limiting; session rotation |
| Extraction accuracy (tables/figures) | **High** | Human review queue; confidence scoring; validation rules |
| Chinese PDF parsing quality | **High** | PaddleOCR + MinerU combination; manual QC for critical data |
| Pipeline reliability | **Medium** | Retry logic; dead-letter queue; checkpoint/resume |

### 6.3 Build vs Buy Assessment

| Component | Recommendation | Rationale |
|-----------|---------------|-----------|
| Search aggregation | **Build** (thin wrapper over free APIs) | APIs are well-documented; no need for paid aggregation |
| PDF download (API path) | **Build** (API client wrappers) | Straightforward HTTP clients |
| PDF download (scraping) | **Buy** (BrightData) | Anti-bot is a solved problem; don't reinvent |
| PDF parsing | **Hybrid** (MinerU + custom extraction) | MinerU handles layout; custom logic for property schema mapping |
| Reference management | **Buy** (Zotero MCP) | Battle-tested; don't rebuild reference management |
| Pipeline orchestration | **Build** (Python MCP client) | Domain-specific; no off-the-shelf solution |

## 7. Integration with NFM Data Model

The pipeline populates these NFM tables:

- `data_sources` — journal articles, reports, theses
- `authors` — paper authors linked to data sources
- `data_source_authors` — many-to-many relationship
- `property_measurements` — extracted values with `data_source_id` FK
- `measurement_conditions` — experimental conditions linked to measurements
- `materials` — may discover new material variants or aliases
- `material_aliases` — alternative names found in literature

### Provenance Chain

```
Paper (data_source) → Page → Table/Figure → Row → Cell (property_measurement)
                                                              ↓
                                                    measurement_conditions
                                                              ↓
                                                    material (via material_id)
```

## 8. Refined Architecture: Integrated Literature Pipeline

Based on the research findings, the recommended pipeline combines:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     NFM Literature Acquisition Pipeline                      │
├─────────────────┬──────────────────┬──────────────────┬────────────────────┤
│   DISCOVERY     │   ACQUISITION    │   EXTRACTION     │   INGESTION        │
├─────────────────┼──────────────────┼──────────────────┼────────────────────┤
│ OpenAlex API    │ Elsevier API     │ MinerU MCP       │ NFM Platform API   │
│ Semantic Scholar│ Springer API     │ GROBID           │ data_sources       │
│ CrossRef        │ BrightData MCP   │ Camelot          │ authors            │
│ SerpApi Scholar │ (fallback)       │ PaddleOCR        │ property_measure.  │
│ cnki-mcp-server │ Zotero MCP       │ LLM extraction   │ measurement_cond.  │
│ litscout        │ (organize/PDF)   │ plotdigitizer    │                    │
└─────────────────┴──────────────────┴──────────────────┴────────────────────┘
         │                  │                   │                   │
         ▼                  ▼                   ▼                   ▼
   Paper metadata    PDFs + provenance   Structured JSON     PostgreSQL tables
   + relevance       + Zotero library    + confidence score   + full provenance
```

### 8.1 Layer 1: Discovery (Cost: Free)

Use open APIs first, commercial search as enrichment:

1. **Primary:** OpenAlex (270M+ works, free, citation graphs) + Semantic Scholar (relevance ranking)
2. **Discovery enrichment:** Google Scholar via SerpApi (broad coverage, handles CAPTCHAs)
3. **Chinese sources:** cnki-mcp-server (Playwright-based, 15 search types)
4. **Screening:** litscout (LLM-powered relevance filtering against nuclear materials ontology)
5. **Metadata enrichment:** CrossRef (DOI resolution, licensing) + Unpaywall (OA PDF locator)

### 8.2 Layer 2: Acquisition (Cost: Free tier + institutional access)

Priority-ordered download strategy:

1. **Open Access first:** Unpaywall + arXiv + CORE (no auth required)
2. **Publisher APIs:** Elsevier API key + Springer API (institutional subscription required)
3. **BrightData MCP fallback:** For sources without API access (free 5K requests/month)
4. **Zotero MCP:** Organize downloaded PDFs, manage metadata, deduplication
5. **Dead letter queue:** Papers that can't be downloaded are queued for manual review

### 8.3 Layer 3: Extraction (Cost: Compute only)

Multi-stage extraction pipeline:

1. **PDF → Markdown:** MinerU MCP (already in our toolchain, Chinese text support)
2. **Structure parsing:** GROBID (header/body/section segmentation)
3. **Table extraction:** Camelot + pdfplumber (for property data tables)
4. **Plot digitization:** plotdigitizer (for extracting data from figures/graphs)
5. **LLM extraction:** Claude/GPT with structured schema prompts aligned to NFM data model
6. **Confidence scoring:** Each extracted value gets a confidence score; low-confidence → human review

### 8.4 Layer 4: Ingestion (Cost: Compute only)

Direct API integration with NFM platform:

1. Create `data_sources` record (paper metadata, DOI)
2. Create `authors` + `data_source_authors` records
3. Create `property_measurements` records (extracted values)
4. Create `measurement_conditions` records (experimental parameters)
5. Queue for verification (nucpot-autovc pipeline or human review)

## 9. Recommended Next Steps

### Phase 1: Prototype (2-3 weeks, Lead Engineer)
1. Wire up OpenAlex + Semantic Scholar + CrossRef APIs in Python
2. Test MinerU MCP on 10 sample nuclear materials papers
3. Design extraction schema mapping (paper table → NFM property_measurements)
4. Validate extraction accuracy against manually curated data

### Phase 2: Production Pipeline (4-6 weeks, Lead Engineer)
5. Integrate BrightData MCP for full-text access
6. Set up Zotero MCP for reference management
7. Build cnki-mcp-server integration for Chinese sources
8. Implement the 4-stage pipeline with dead-letter queue

### Phase 3: Verification & Scaling (ongoing)
9. Connect to nucpot-autovc verification pipeline
10. Add confidence scoring and human review queue
11. Scale to target material systems (UO₂, Zr-alloys, SiC, etc.)

### Prerequisites (CEO decisions needed)
- **Budget:** BrightData free tier for pilot; $0 cost if using open APIs + institutional access
- **Legal:** Review ToS for automated CNKI and ScienceDirect access
- **Institutional access:** Confirm Elsevier/Springer API key eligibility
- **Priority:** Define first batch of material systems and property types to target

## 10. Key Findings Summary

| Finding | Impact |
|---------|--------|
| BrightData offers 5,000 free requests/month + academic pro-bono program | Pilot can be zero-cost |
| cnki-mcp-server exists on PyPI with Playwright automation | Chinese literature access is technically solved |
| Zotero MCP has 2,865-star Python implementation with CNKI support | Reference management is production-ready |
| elsevier-mcp provides direct Claude Code integration | ScienceDirect access has existing MCP tooling |
| MinerU MCP already in our toolchain with Chinese OCR | PDF extraction layer requires no new dependencies |
| litscout provides AI-powered multi-source screening | Discovery/screening stage has off-the-shelf solution |
| OpenAlex + Semantic Scholar offer free, comprehensive APIs | Discovery stage can be entirely free |

**Overall assessment:** The technical gaps are addressable with existing tools. No fundamental technology gaps remain — the challenge is integration, legal compliance, and extraction accuracy. The recommended approach uses free/open tools for 80% of the pipeline, with BrightData as a paid fallback for anti-bot protected sources.
