# Fix: Verification Results Not Displayed on Overview Tab

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the overview tab on potential detail pages so that verification grades stored in Format B (`{cohesive_energy: {value, grade, ...}}`) are properly displayed with grade badges and a results table.

**Architecture:** The frontend has two tabs that render verified_props:
- **Properties tab** (`"properties"===g`) — uses `parseVerifiedProps()` → works ✅
- **Overview tab** (`"overview"===g`) — directly reads `e.overall_grade` and `e.results` → **fails for Format B** ❌

The fix adds Format B → Format A auto-conversion to the overview tab, matching the existing source code that was committed locally but never deployed. We also need to trigger a Vercel deployment.

**Tech Stack:** Next.js 15 (App Router), React, TypeScript, Tailwind CSS, Vercel deployment

---

## Context

### File: `src/app/potential/[id]/page.tsx`

This is the potential detail page. It has two rendering sections for verification data:

1. **Properties tab** (lines ~335-388): Uses `parseVerifiedProps()` which handles both flat values and nested objects. **Works correctly.**

2. **Overview tab** (lines ~487-595): Has two format support:
   - Format A: `{ overall_grade, results: [{property_name, computed_value, ...}] }`
   - Format B: `{ cohesive_energy: {value, reference, grade, ...}, lattice_constant: {...} }`
   
   The local source code has Format B auto-conversion (commit 1224ca0), but the **deployed Vercel version only handles Format A**.

### Two verified_props formats

**Format A** (written by new verify-service):
```json
{
  "overall_grade": "A",
  "verified_at": "...",
  "source": "...",
  "results": [{"property_name": "...", "computed_value": 3.165, "reference_value": 3.165, "unit": "angstrom", "relative_error": 0, "grade": "A"}]
}
```

**Format B** (existing data in DB):
```json
{
  "cohesive_energy": {"unit": "eV/atom", "grade": "B", "value": -8.9, "reference": -8.6, "absolute_error": 0.3, "relative_error": 0.035},
  "lattice_constant": {"unit": "angstrom", "grade": "A", "value": 3.165, "reference": 3.165, "absolute_error": 0, "relative_error": 0}
}
```

---

### Task 1: Add Format B auto-conversion to overview tab

**Files:**
- Modify: `src/app/potential/[id]/page.tsx` (overview tab section, ~lines 487-595)

**Context:** The overview tab verification section starts with:
```tsx
{activeTab === 'overview' && (() => {
  const vp = p.verified_props as Record<string, any> | null
  if (!vp || Object.keys(vp).length === 0) {
    return (/* "尚未验证" message */)
  }
```

After the empty check, it currently reads:
```tsx
  let overallGrade = vp.overall_grade as string | undefined
  let results = vp.results as Array<...> | undefined
```

**Problem:** When data is Format B, `vp.overall_grade` is undefined and `vp.results` is undefined, so the table renders empty.

- [ ] **Step 1: Add Format B → Format A conversion block after the `results` variable**

Insert this conversion block right after `let results = vp.results as ... | undefined` and before the `return`:

```tsx
          // Auto-convert Format B → Format A
          if (!results) {
            const labels: Record<string, string> = { lattice_constant: '晶格常数', cohesive_energy: '结合能', bulk_modulus: '体弹模量', elastic_constant: '弹性常数', vacancy_formation_energy: '空位形成能', surface_energy: '表面能', shear_modulus: '剪切模量' }
            const converted: typeof results = []
            let grades: string[] = []
            for (const [key, raw] of Object.entries(vp)) {
              if (typeof raw === 'object' && raw !== null && 'value' in (raw as Record<string, unknown>)) {
                const d = raw as Record<string, any>
                if (d.value == null || d.error) continue
                converted.push({
                  property_name: labels[key] || key,
                  computed_value: d.value,
                  reference_value: d.reference ?? 0,
                  unit: d.unit || '',
                  relative_error: d.relative_error ?? (d.reference ? Math.abs(d.value - d.reference) / Math.abs(d.reference) : 0),
                  grade: d.grade || 'N/A',
                })
                if (d.grade) grades.push(d.grade)
              }
            }
            if (converted.length > 0) {
              results = converted
              if (!overallGrade && grades.length > 0) {
                const gradeOrder = ['A', 'B', 'C', 'D']
                overallGrade = grades.sort((a, b) => gradeOrder.indexOf(a) - gradeOrder.indexOf(b))[0]
              }
            }
          }
```

This is the exact same code that already exists in the local source but is missing from the deployed version.

- [ ] **Step 2: Verify the fix locally**

Run: `cd ~/projects/nucpot && npm run build 2>&1 | tail -5`
Expected: Build succeeds with no errors

- [ ] **Step 3: Commit**

```bash
cd ~/projects/nucpot && git add src/app/potential/[id]/page.tsx && git commit -m "fix: add Format B auto-conversion for overview tab verified_props

The overview tab only handled Format A ({overall_grade, results: [...]}).
Existing data in Supabase uses Format B ({cohesive_energy: {value, grade, ...}}).
Add auto-conversion logic to support both formats, matching the properties tab behavior."
```

---

### Task 2: Deploy to Vercel

**Files:** None (deployment task)

- [ ] **Step 1: Check how Vercel deployment is configured**

Run: `cd ~/projects/nucpot && git remote -v && cat package.json | grep -A5 '"scripts"'`
Also check: `ls .vercel/ 2>/dev/null; cat vercel.json 2>/dev/null`

- [ ] **Step 2: Push to trigger Vercel auto-deploy, or manually deploy**

If Vercel is connected to GitHub:
```bash
cd ~/projects/nucpot && git push origin main
```

If no auto-deploy:
```bash
cd ~/projects/nucpot && npx vercel --prod
```

- [ ] **Step 3: Verify deployment**

Run: `curl -sI "https://nucpot.dpdns.org" | grep "age:"`
Expected: `age: 0` or very low value (fresh deployment)

Then verify the specific page renders verification data by checking the JS chunk:
```bash
# Download the updated chunk and search for Format B conversion code
curl -s "https://nucpot.dpdns.org/potential/c6591f31-2171-4e4e-a785-94d587cc8920" | python3 -c "
import re, urllib.request, sys
html = sys.stdin.read()
chunks = re.findall(r'src=\"(/_next/static/chunks/[^\"]+\.js)\"', html)
for c in chunks:
    url = f'https://nucpot.dpdns.org{c}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='replace')
    if 'Auto-convert' in data or 'cohesive_energy' in data:
        print(f'Format B conversion code found in: {c}')
        if 'lattice_constant' in data and 'labels[key]' in data:
            print('✅ Format B conversion verified in deployed code')
        break
"
```

Expected: `✅ Format B conversion verified in deployed code`
