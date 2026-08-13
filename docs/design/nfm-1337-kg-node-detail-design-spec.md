# NFM-1337 — KG Node Detail Page Design Specification

**Issue**: NFM-1337 · **Parent**: NFM-983 · **Author**: UXDesigner · **Date**: 2026-07-13

---

## 1. Route & File Structure

```
apps/web/src/app/kg/nodes/[type]/[id]/
├── page.tsx              # Server component — unwraps params, renders client shell
└── NodeDetailContent.tsx  # Client component — data fetching, layout, interaction
```

Follows the same pattern as `kg/search/page.tsx` → `KgSearchContent.tsx` (server wrapper + client body).

---

## 2. Layout Architecture

### 2.1 Viewport Breakpoints

| Breakpoint | Layout | Detail Width | Sidebar Width | Gutter |
|-----------|--------|-------------|--------------|--------|
| ≥1024px (lg) | Two-column | `flex-1` (~60%) | `w-[380px]` fixed | `gap-6` |
| 768–1023px (md) | Two-column | `flex-1` | `w-[320px]` | `gap-4` |
| <768px | Single-column | Full width | Full width (below main) | `space-y-6` |

### 2.2 Page Shell

```tsx
<main className="max-w-[1200px] mx-auto px-6 py-8">
  {/* Breadcrumb */}
  <nav aria-label="Breadcrumb" className="mb-4">
    <ol className="flex items-center gap-1.5 text-sm text-gray-400">
      <li><a href="/kg/search" className="hover:text-blue-300 transition-colors">Knowledge Graph</a></li>
      <li aria-hidden="true" className="text-gray-600">/</li>
      <li><a href="/kg/search" className="hover:text-blue-300 transition-colors">Search</a></li>
      <li aria-hidden="true" className="text-gray-600">/</li>
      <li className="text-gray-200" aria-current="page">{node.label}</li>
    </ol>
  </nav>

  {/* Content grid */}
  <div className="flex flex-col lg:flex-row gap-6">
    <div className="flex-1 min-w-0">{/* Main: Header + Properties + Sources */}</div>
    <aside className="w-full lg:w-[380px] shrink-0">{/* Sidebar: Relations */}</aside>
  </div>
</main>
```

**Token compliance**: `max-w-[1200px]`, `px-6`, `py-8`, `gap-6` — matches `kg/search` page wrapper exactly.

---

## 3. Component Specifications

### 3.1 Node Header

**Position**: Top of main content column, below breadcrumb.

```tsx
<header className="mb-6">
  <div className="flex flex-wrap items-center gap-2 mb-2">
    <NodeTypeBadge nodeType={node.node_type} />
    <StatusBadge status={node.status} />
    <ConfidenceBadge value={node.confidence} size="sm" showLabel={false} />
  </div>
  <h1 className="text-2xl font-bold text-white leading-tight">{node.label}</h1>
  {node.aliases.length > 0 && (
    <p className="mt-1 text-sm text-gray-400">
      Also known as: {node.aliases.join(', ')}
    </p>
  )}
</header>
```

**`NodeTypeBadge`** — NEW component (inline in file, not shared):

| Prop | Type | Description |
|------|------|-------------|
| `nodeType` | `string` | One of KG_NODE_TYPES |

**Rendering rules**:
- Use `<span>` with `inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border`.
- Color map (aligned to `graph-theme.ts` canonical tokens, **NOT** the inconsistent `KgSearchContent` map):

```
Material:    bg-emerald-500/20 text-emerald-300 border-emerald-500/30
Property:    bg-amber-500/20   text-amber-300   border-amber-500/30
Entity:      bg-violet-500/20  text-violet-300  border-violet-500/30
Experiment:  bg-blue-500/20    text-blue-300    border-blue-500/30
Condition:   bg-orange-500/20  text-orange-300  border-orange-500/30
Publication: bg-rose-500/20    text-rose-300    border-rose-500/30
(fallback):  bg-gray-500/20    text-gray-300    border-gray-500/30
```

> **Design debt note**: `KgSearchContent.tsx` line 27–33 uses `blue-500` for Material and `green-500` for Property, which contradicts the canonical graph-theme tokens (`#34d399`/emerald for Material, `#fbbf24`/amber for Property). The node detail page uses the correct graph-theme mapping. A follow-up should align the search page.

**`StatusBadge`** — inline, no shared component needed:

| Status | Tailwind classes |
|--------|-----------------|
| `active` | `bg-emerald-500/20 text-emerald-300 border-emerald-500/30` |
| `pending_review` | `bg-amber-500/20 text-amber-300 border-amber-500/30` |
| `merged` | `bg-blue-500/20 text-blue-300 border-blue-500/30` |
| `deprecated` | `bg-red-500/20 text-red-300 border-red-500/30` |

Same `<span>` shape as NodeTypeBadge.

**`ConfidenceBadge`** — REUSE existing `@/components/shared/ConfidenceBadge`. Props: `value={node.confidence}` `size="sm"` `showLabel={false}`.

---

### 3.2 Properties Section

**Position**: Main content column, below header.

```tsx
<section aria-labelledby="properties-heading" className="mb-6">
  <h2 id="properties-heading" className="text-lg font-semibold text-white mb-3">
    Properties
  </h2>
  {Object.keys(node.properties).length === 0 ? (
    <Empty description="No properties recorded for this node." />
  ) : (
    <div className="rounded-lg border border-[var(--border-color,#2d2d44)] overflow-hidden">
      <table className="w-full text-sm" role="table">
        <thead>
          <tr className="bg-[var(--bg-elevated,#1a1a2e)] border-b border-[var(--border-color,#2d2d44)]">
            <th className="text-left px-4 py-2.5 text-gray-400 font-medium">Key</th>
            <th className="text-left px-4 py-2.5 text-gray-400 font-medium">Value</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(node.properties).map(([key, value]) => (
            <tr key={key} className="border-b border-[var(--border-color,#2d2d44)] last:border-b-0 hover:bg-[var(--bg-elevated-hover,#22223a)] transition-colors">
              <td className="px-4 py-2.5 text-gray-300 font-mono text-xs">{key}</td>
              <td className="px-4 py-2.5 text-white break-all">{String(value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )}
</section>
```

**Token compliance**: `border-[var(--border-color,#2d2d44)]`, `bg-[var(--bg-elevated,#1a1a2e)]` — matches search page card styling.

**Notes**:
- Use native `<table>` with ARIA roles (NOT Ant Design Table — the data is simple key-value, not paginated/sortable).
- `font-mono text-xs` for property keys (scientific data convention).
- `break-all` on values to prevent overflow from long strings.

---

### 3.3 Source References Section

**Position**: Main content column, below properties.

```tsx
{node.source_id && (
  <section aria-labelledby="source-heading" className="mb-6">
    <h2 id="source-heading" className="text-lg font-semibold text-white mb-3">
      Source
    </h2>
    <div className="p-4 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)]">
      <p className="text-sm text-gray-400">
        Extracted from document{' '}
        <a
          href={`/materials/${node.source_id}`}
          className="text-blue-400 hover:text-blue-300 underline underline-offset-2 transition-colors"
        >
          {node.source_id.slice(0, 8)}…
        </a>
      </p>
      {node.corpus_id && (
        <p className="text-xs text-gray-500 mt-1">Corpus: {node.corpus_id}</p>
      )}
    </div>
  </section>
)}
```

**Token compliance**: Same elevated card tokens as properties section.

---

### 3.4 Relations Sidebar

**Position**: Right column (or below main on mobile).

```tsx
<aside aria-labelledby="relations-heading">
  <h2 id="relations-heading" className="text-lg font-semibold text-white mb-3">
    Relations
  </h2>

  <Ant.Design.Tabs
    defaultActiveKey="outgoing"
    items={[
      {
        key: 'outgoing',
        label: `Outgoing (${outgoingCount})`,
        children: <RelationList relations={outgoing} direction="outgoing" nodeId={node.id} />,
      },
      {
        key: 'incoming',
        label: `Incoming (${incomingCount})`,
        children: <RelationList relations={incoming} direction="incoming" nodeId={node.id} />,
      },
    ]}
  />
</aside>
```

**`RelationList`** — inline sub-component:

```tsx
function RelationList({ relations, direction, nodeId }: RelationListProps) {
  if (relations.length === 0) {
    return <Empty description="No relations." className="py-8" />
  }

  return (
    <ul className="space-y-2" role="list">
      {relations.map((rel) => {
        const peer = direction === 'outgoing' ? rel.target_node : rel.source_node
        return (
          <li key={rel.relation_type + peer.id}>
            <a
              href={`/kg/nodes/${peer.node_type}/${peer.id}`}
              className="flex items-center gap-2 p-3 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)] hover:border-blue-500/40 hover:bg-[var(--bg-elevated-hover,#22223a)] transition-all duration-150 group"
            >
              <NodeTypeBadge nodeType={peer.node_type} />
              <span className="flex-1 text-sm text-white group-hover:text-blue-300 transition-colors truncate">
                {peer.label}
              </span>
              <span className="text-xs text-gray-500 shrink-0">{rel.relation_type}</span>
            </a>
          </li>
        )
      })}
    </ul>
  )
}
```

**Token compliance**: Same card tokens. Clickable relations are `<a>` elements (not `<button>`) because they navigate to a new route.

**Navigation**: Each relation links to `/kg/nodes/${peer.node_type}/${peer.id}` — same route shape as the current page.

---

### 3.5 Loading State

```tsx
// Replace entire content area with skeleton
<div className="motion-safe:animate-pulse space-y-4">
  {/* Header skeleton */}
  <div className="flex gap-2">
    <div className="h-5 w-20 rounded bg-gray-700" />
    <div className="h-5 w-16 rounded bg-gray-700" />
  </div>
  <div className="h-8 w-64 rounded bg-gray-700" />
  <div className="h-4 w-48 rounded bg-gray-700" />

  {/* Properties skeleton */}
  <div className="h-48 w-full rounded-lg bg-gray-800" />

  {/* Sidebar skeleton */}
  <div className="h-64 w-full rounded-lg bg-gray-800" />
</div>
```

**Do NOT** use `<Spin />` for the initial page load — use skeleton (matches LCP <3.0s AC). Reserve `<Spin />` for relation tab switches only.

---

### 3.6 Error State

```tsx
<div className="flex flex-col items-center justify-center min-h-[300px] gap-4">
  <Typography.Text type="danger">{error}</Typography.Text>
  <Ant.Design.Button icon={<ReloadOutlined />} onClick={handleRetry}>
    Retry
  </Ant.Design.Button>
</div>
```

Reuse Ant Design `Button` + `ReloadOutlined` pattern from search page.

---

### 3.7 Not Found State

```tsx
<div className="flex flex-col items-center justify-center min-h-[300px] gap-4">
  <Ant.Design.Empty description="Node not found." />
  <Ant.Design.Button type="primary" href="/kg/search">
    Back to Search
  </Ant.Design.Button>
</div>
```

Triggered when API returns 404 or node is `null`.

---

## 4. Data Fetching

### 4.1 API Calls

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `/api/v1/kg/nodes/{type}/{id}` | GET | Node detail | **NOT IMPLEMENTED** (NFM-1211) |
| `/api/v1/kg/nodes/{id}/relations` | GET | Incoming/outgoing edges | **NOT IMPLEMENTED** (NFM-1211) |

### 4.2 Mock Strategy (per NFM-1337 memory)

Since both endpoints are unimplemented, ship frontend with `vi.hoisted` fetch mock:

```typescript
// __tests__/kg/node-detail.test.tsx
const mockFetch = vi.hoisted(() => vi.fn())
vi.stubGlobal('fetch', mockFetch)

// Mock node detail response
mockFetch.mockImplementation(async (url: string) => {
  if (url.includes('/nodes/Material/')) {
    return Response.json({
      success: true,
      data: {
        id: 'mock-uuid',
        node_type: 'Material',
        label: 'UO₂',
        aliases: ['Uranium Dioxide', 'UO2'],
        properties: { density: 10.97, crystal_structure: 'Fluorite' },
        confidence: 0.95,
        status: 'active',
        source_id: 'source-uuid',
        corpus_id: 'corpus-001',
      },
    })
  }
  if (url.includes('/relations')) {
    return Response.json({
      success: true,
      data: {
        total: 2,
        items: [
          {
            relation_type: 'hasProperty',
            source_node: { id: 'mock-uuid', node_type: 'Material', label: 'UO₂' },
            target_node: { id: 'prop-uuid', node_type: 'Property', label: 'Density' },
          },
          {
            relation_type: 'testedAt',
            source_node: { id: 'mock-uuid', node_type: 'Material', label: 'UO₂' },
            target_node: { id: 'exp-uuid', node_type: 'Experiment', label: 'Irradiation Test #42' },
          },
        ],
      },
    })
  }
  return new Response('Not found', { status: 404 })
})
```

### 4.3 TypeScript Interfaces

```typescript
// apps/web/src/lib/kg-node-detail-api.ts

interface KgNodeDetail {
  readonly id: string
  readonly node_type: string
  readonly label: string
  readonly aliases: readonly string[]
  readonly properties: Readonly<Record<string, unknown>>
  readonly confidence: number
  readonly status: 'active' | 'merged' | 'deprecated' | 'pending_review'
  readonly source_id: string | null
  readonly corpus_id: string | null
  readonly figure_id: string | null
}

interface KgRelation {
  readonly relation_type: string
  readonly source_node: Readonly<{ id: string; node_type: string; label: string }>
  readonly target_node: Readonly<{ id: string; node_type: string; label: string }>
}

interface KgRelationsResponse {
  readonly total: number
  readonly items: readonly KgRelation[]
}
```

---

## 5. Accessibility Requirements

| Requirement | Implementation |
|-------------|---------------|
| Keyboard navigation | All interactive elements are native `<a>` or `<button>` — keyboard focusable by default |
| Focus indicators | `focus:ring-2 focus:ring-blue-500/50` on all clickable cards (matches search page) |
| Screen reader | `aria-label` on breadcrumb nav, `aria-labelledby` on sections, `role="table"` on properties |
| Reduced motion | Use Tailwind `motion-safe:animate-pulse` on skeleton (respects `prefers-reduced-motion`) |
| Color contrast | All text meets WCAG AA against dark background (#111827). Verified: gray-200 (#e5e7eb) on #111827 = 13.4:1 pass |

---

## 6. Performance Targets

| Metric | Target | Strategy |
|--------|--------|----------|
| LCP | <3.0s | Skeleton renders immediately; data fills in async |
| CLS | <0.1 | Fixed header height, no late-loading images |
| INP | <200ms | No heavy client-side computation; simple state updates |

**No images on this page** — pure data display. This keeps LCP well under target.

---

## 7. Interaction Specifications

| Element | Trigger | Behavior |
|---------|---------|----------|
| Relation card | Click | Navigate to `/kg/nodes/{target_type}/{target_id}` via Next.js `<a>` (full page navigation, not client-side state) |
| Breadcrumb "Search" | Click | Navigate to `/kg/search` |
| Source link | Click | Navigate to `/materials/{source_id}` |
| Retry button | Click | Re-fetch both node detail and relations |
| Tab switch | Click | Switch between outgoing/incoming relation lists (instant, no fetch needed — data loaded once) |

---

## 8. Design Debt

| Item | Impact | Recommendation |
|------|--------|----------------|
| `KgSearchContent` TYPE_COLORS mismatch with `graph-theme.ts` | Inconsistent badge colors across KG pages | Align search page to graph-theme tokens in a follow-up PR |
| No `<DataTable>` shared component | Properties table is hand-rolled | Acceptable for simple key-value; consider shared component if more tables appear |
| Backend endpoints not implemented | Page ships with mocked data | Coordinate with NFM-1211 for backend delivery |

---

## 9. Handoff Checklist for Lead Engineer

- [ ] Create `apps/web/src/app/kg/nodes/[type]/[id]/page.tsx` (server wrapper)
- [ ] Create `apps/web/src/app/kg/nodes/[type]/[id]/NodeDetailContent.tsx` (client body)
- [ ] Create `apps/web/src/lib/kg-node-detail-api.ts` (types + fetch helpers)
- [ ] Mock both API endpoints with `vi.hoisted` pattern in tests
- [ ] Use `getAllByText` for legitimate text duplicates across sources/relations
- [ ] Breadcrumb nav from `/kg/search` → current node label
- [ ] Properties table with `font-mono` keys
- [ ] Relations sidebar with `Ant.Design.Tabs` (outgoing/incoming)
- [ ] Relation links navigate to `/kg/nodes/{peer.node_type}/{peer.id}`
- [ ] Loading skeleton (NOT `<Spin>`) for initial page load
- [ ] Error + retry + not-found states
- [ ] `prefers-reduced-motion` respected on skeleton animation
- [ ] Keyboard accessible (all native elements)
- [ ] Post visual QA screenshots at 1440x900 (desktop) and 390x844 (mobile) before requesting review

---

## 10. UX Visual QA Review — Round 1 (2026-07-13)

**Branch:** `feat/nfm-1337-kg-node-detail` @ commit `b89c249`
**Reviewer:** UXDesigner
**Verdict:** REQUIRES CHANGES — 3 spec deviations

> Per my Visual-Truth Gate: I cannot formally pass this review without screenshots at 1440x900 desktop and 390x844 mobile. The backend endpoint (GET /api/v1/kg/nodes/{type}/{id}) is not implemented, so no live render is possible. I am therefore reviewing against this design spec at the code level. Once the backend lands, Lead Engineer **MUST** provide visual screenshots before I sign off.

> Actor boundary note: This review was written as a durable file because the issue (NFM-1337) is assigned to Lead Engineer, blocking all POST comment and PATCH operations from UXDesigner. CPO or system must reassign before formal handoff.

### FINDINGS (require changes before pass)

#### F1: Loading state uses Spin instead of skeleton (Spec section 3.5 MANDATORY)

Spec says: Do NOT use Spin for the initial page load — use skeleton (matches LCP < 3.0s AC).
Implementation: Both the server Suspense fallback AND the client loading state render Spin tip="Loading node...".
Fix: Replace with skeleton matching section 3.5.

#### F2: Relations sidebar uses button + router.push instead of anchor links (Spec section 3.4)

Spec says: Clickable relations are anchor elements (not button) because they navigate to a new route.
Implementation: RelationRow renders button with onClick + router.push().
Impact: Breaks right-click open-in-new-tab, Ctrl+click, hover URL preview, and anchor semantics for assistive tech.
Fix: Use anchor href="/kg/nodes/${neighbour_type}/${neighbour_id}" instead.

#### F3: TYPE_COLORS duplicates KgSearchContent instead of graph-theme canonical tokens (Spec section 3.1)

Spec section 3.1 explicitly says: Color map aligned to graph-theme.ts canonical tokens, NOT the inconsistent KgSearchContent map — Material=emerald, Property=amber, Entity=violet, Experiment=blue, Condition=orange, Publication=rose.
Implementation: Copies KgSearchContent inconsistent map verbatim: Material=blue, Property=green.
Fix: Align to canonical graph-theme tokens.

### SPEC MATCHES (verified)

| Spec Item | Status |
|-----------|--------|
| Node header: type badge + label + confidence + status + aliases | PASS |
| Properties table: native table, font-mono keys, CSS variables | PASS |
| Source references section | PASS |
| Relations sidebar: incoming + outgoing with counts | PASS |
| Relation click navigates to peer node detail | PASS |
| Error state + retry button | PASS |
| Keyboard accessible — native elements + focus rings | PASS |
| aria-labelledby on all sections | PASS |
| prefers-reduced-motion honored | PASS |
| Server component Suspense shell | PASS |
| Typed API client with percent-encoding | PASS |
| Two-file pattern (page.tsx + KgNodeDetailContent.tsx) | PASS |

### REQUIRED BEFORE VISUAL PASS

1. **F1**: Replace Spin with skeleton loading state
2. **F2**: Change relation button to anchor with href
3. **F3**: Align TYPE_COLORS to graph-theme canonical tokens

After these 3 fixes + backend implementation, Lead Engineer must provide screenshots at 1440x900 (desktop) and 390x844 (mobile).
