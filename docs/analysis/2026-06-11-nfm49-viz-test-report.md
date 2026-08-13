# OntoFuel NVL Visualization: Test Report & Improvement Plan

**Date**: 2026-06-11
**Issue**: NFM-49
**CTO Assessment Session**

## Environment

- **Repo**: `ontofuel-nvl-visualization` (https://github.com/Etoile04/ontofuel-nvl-visualization)
- **Local**: `/Users/lwj04/.openclaw/workspace-extractor/visualization-app`
- **Stack**: React 18 + TypeScript 5 + Neo4j NVL 1.1.0 + CRA (react-scripts 5.0.1)

## Unit Tests

- **Result**: 23/23 PASS ✅
- **Runtime**: 1.045s
- **Actual coverage**: 41% stmts / 38% branches / 33% functions / 41% lines
- **Gaps**: `exportUtils.ts` (17%), `helpers.ts` (0%)

## Production Build

- **Result**: Compiled successfully ✅
- **Bundle**: 521KB gzipped (main.js), 155KB (CoseBilkentLayout chunk), 48KB (HierarchicalLayout chunk)

## Visual / Functional Testing (Chrome browser)

- **Graph rendering**: ✅ 926 nodes, 1055 relationships display correctly in force-directed layout
- **Stats panel**: ✅ Classes: 156, Individuals: 755, Hierarchy: 139, Properties: 162
- **Search input**: ✅ Accepts text, triggers filter
- **Search rendering**: ❌ **BUG** — filtered nodes (85 for "Uranium") produce blank canvas
- **Console errors**: ✅ None
- **Layout switching**: Not tested (UI element present)
- **Export menu**: Not tested (UI element present)
- **Node click details**: Not tested

## Pipeline Testing

- **ontology_to_nvl.py**: ✅ Converts `material_ontology_enhanced.json` → NVL JSON (926 nodes, 1055 rels, 0.1s)
- **sync_viz_pipeline.py**: ✅ End-to-end (ontology → NVL → standalone HTML, 0.1s, 1031KB output)
- **generate_standalone_viz.py**: ✅ Produces self-contained HTML with embedded D3.js

---

## Issues Found

### 1. Search Filter Rendering Bug (HIGH)

**Symptom**: Searching filters nodes correctly but NVL canvas shows blank.

**Root Cause**: `filteredNodes` passed to `InteractiveNvlWrapper` as `nodes` prop, but `rels` still receives unfiltered relationships. NVL may suppress disconnected nodes.

**Fix**: Add `filteredRelationships` useMemo that filters rels to only include connections between filtered nodes.

→ **Child issue**: NFM-50

### 2. Test Coverage Gap (MEDIUM)

Actual: 41%. Required: 80%. The export utilities and helpers have almost no test coverage.

→ **Child issue**: NFM-51

### 3. No REST API for NVL Data (Architecture)

Static-only data loading. For NFMD pipeline integration, need a dynamic API endpoint.

→ **Child issue**: NFM-52

---

## Child Issues Created

| Issue | Title | Priority | Status |
|-------|-------|----------|--------|
| NFM-50 | Fix NVL search filter rendering bug | HIGH | todo |
| NFM-51 | Improve test coverage to 80%+ | MEDIUM | todo (blocked by NFM-50) |
| NFM-52 | Add REST API endpoint for NVL data | MEDIUM | todo |

## Improvement Plan

### Phase 2 (Bug Fix + Coverage)
- NFM-50: Fix search filter → unblocks NFM-51
- NFM-51: Add tests for exportUtils.ts, helpers.ts, component interactions

### Phase 3 (Pipeline Integration)
- NFM-52: REST API in FastAPI backend to serve NVL data dynamically

### Future Considerations (Not tracked)
- Migrate from CRA to Vite (bundle size optimization)
- Add CI/CD pipeline (GitHub Actions)
- WebSocket for live ontology updates
- Color-coded node types (currently all green)
