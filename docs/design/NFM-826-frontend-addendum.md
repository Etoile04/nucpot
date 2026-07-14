# NFM-826 Frontend Addendum

This document records deviations from the NFM-826 design specification made during implementation on the `feat/nfm-834-frontend-compliance` branch, along with the rationale for each.

## Deviations

### 1. Route: `/kg/explore` → `/kg/search`

- **Spec:** `/kg/explore`
- **Implemented:** `/kg/search`
- **Rationale:** The "search" label is more descriptive of the actual functionality. The page is a search interface over the knowledge graph, not an open-ended exploration tool. Using `/search` aligns the route name with user intent and is consistent with other search-oriented routes in the application.

### 2. Route: `/extraction/*` → `/admin/v4-extraction/*`

- **Spec:** `/extraction/*`
- **Implemented:** `/admin/v4-extraction/*`
- **Rationale:** The extraction interface is an administrative operation, so it belongs under the `/admin` namespace. The `v4-` prefix reflects the version of the extraction pipeline (V4) that these pages target, disambiguating it from any future extraction versions and keeping the route self-documenting.

### 3. Component: `CitationTooltip` → `CitationCard`

- **Spec:** `CitationTooltip`
- **Implemented:** `CitationCard`
- **Rationale:** A citation typically displays multiple fields (title, authors, source, DOI, relevance snippet, etc.). A tooltip is too constrained for this content, whereas a card layout provides adequate space and structure for multi-field display. The card component is also reusable across list views and detail pages.

### 4. Component: `NodeDetailSidebar` → `NodeDetailContent` (page-level)

- **Spec:** `NodeDetailSidebar` (shared component)
- **Implemented:** `NodeDetailContent` (page-level component)
- **Rationale:** The node detail view is currently used in only one route. Extracting it into a shared sidebar component would introduce premature abstraction with no immediate consumer. The page-level `NodeDetailContent` component keeps the implementation simple; extraction to a shared component is deferred until a second consumer is identified, at which point the refactor cost is low.

## Summary

| # | Spec | Implemented | Reason |
|---|------|-------------|--------|
| 1 | `/kg/explore` | `/kg/search` | "search" is more descriptive of actual functionality |
| 2 | `/extraction/*` | `/admin/v4-extraction/*` | Admin namespace + v4 version prefix |
| 3 | `CitationTooltip` | `CitationCard` | Card layout more suitable for multi-field citation display |
| 4 | `NodeDetailSidebar` | `NodeDetailContent` | Single-route usage; shared component deferred until reuse needed |
