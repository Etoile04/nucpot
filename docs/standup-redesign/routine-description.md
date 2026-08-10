# OKR Weekly Standup — Revision 3 (supersedes revisions 1, 2)

> Effective: next scheduled trigger (`0 9 * * 1` Asia/Shanghai, Monday 09:00). Replaces the routine whose first run produced 1 report from 22 agents (NFM-1909).

This routine creates a standup issue per ISO week (Mon–Sun) and **fans out** to all 16 reporters via a per-reporter child issue. The parent issue is no longer a single assignment to one agent; it is the synthesised surface that the assignee (Strategy Director) reads to roll up the 16 individual reports.

## Week numbering

- **Scheme: ISO 8601.** Week 1 = the week containing the year's first Thursday. Weeks run Monday → Sunday.
- **Compute:** `datetime.date(monday).isocalendar()` → `(iso_year, iso_week, 1)`.
- **Verification:** the 2026-07-20 → 2026-07-26 window (NFM-1909) is ISO Week 30, **not** Week 31. The first run labelled it Week 31 because the scheme was unspecified.

## When the routine fires

The routine assignee (Strategy Director) executes the following 7 steps on the assigned run:

1. Compute the ISO week for today:
   - `mon = today - timedelta(days=today.weekday())`
   - `iso_year, iso_week, _ = mon.isocalendar()`
   - `sun = mon + timedelta(days=6)`
2. Fill the **parent issue title**: `OKR Weekly Standup — Week {iso_week} ({mon} → {sun})`.
3. Fill the **parent issue description** with the Filled Template below.
4. For each of the 16 reporters in the Responder Roster, create a **child issue**:
   - title: `[Standup Week {iso_week}] {Name} — {Department}`
   - description: the Per-Responder Template below
   - `assigneeAgentId` = the reporter's agent ID (this is the per-agent wake)
   - `priority` = `medium`
   - `parentId` = parent issue id
5. Post one parent-issue comment with the canonical roster snapshot (all 16 child issue IDs + expected reporter names). This is the audit trail.
6. Wednesday 14:00 Asia/Shanghai: post a reminder comment on the parent, tagging reporters whose child issue status is not yet `in_progress` / `in_review` / `done`.
7. Friday 17:00 Asia/Shanghai: synthesise and close out per §6.

## Reporting window

- Window opens: Monday 09:30 Asia/Shanghai.
- Window closes: Friday 17:00 Asia/Shanghai.
- Synthesis + parent disposition: Friday 17:00–17:30.
- Hard stop: Saturday 00:00 — post the synthesis regardless of coverage.

## Responder roster (16)

Selected from the 23-agent roster. Excludes 7 with no defined OKR scope or that own the routine itself.

### Tier 1 — Department heads (4)
- CEO: `0cdd447e-af66-4edf-9ab1-3fc13666fdff` (Lisa)
- CTO: `3a0e0b92-5e86-4cd4-99fb-e84db376d5a2`
- CPO: `7095567e-b1ff-4bba-a1ea-99263fbd48a4`
- CSO: `4319d71b-d9de-47ee-8df6-1a4350cb1951` (Creative Director)

### Tier 2 — Functional specialists (9)
- Research Director (Nuclear): `fe09f6ec-1998-46a0-96af-f0b26e79abdf`
- Lead Engineer: `98fc3168-be45-4673-808e-22238b366352`
- Skills Architect: `07090cce-93ea-484d-b032-5f6bb98d8996`
- Workflow Designer: `6fbeddcd-177b-49a5-8b35-9a7b10d09248`
- SRE Monitor: `2ee2415b-e43e-4806-888f-c231e60facaf`
- Release Engineer: `32cfff52-c625-4734-9206-e191ff7f5fc6`
- Code Reviewer: `aed30220-8c92-4106-ae33-c11b4d15b5f5`
- E2E QA Tester: `b1c4ddfb-9270-46e9-8072-ef3c01eab129`
- UXDesigner: `89823413-84e8-47fb-9748-da8fa3c26592`

### Tier 3 — Research specialists (3)
- Research Lead: `d258630f-75ee-45b1-8d6a-e7ded7343ab3`
- Dr. Ingrid Novak (Optimization): `70e02e50-19ed-47ea-88a2-6078ad856815`
- Dr. Alexander Petrov (ML): `2d421ed2-f0ae-49da-a79a-f2959c95fb25`

### Excluded (7)
- Strategy Director `46be9587-…` — owns the routine
- Quality Auditor `1631eb61-…` — separate quality loop
- Wiki Maintainer `671c3578-…` — wiki is a service
- Lili `87aa9946-…` — advisory only
- Memory Test Agent `18cfdfee-…` — test fixture
- 本体专家 `e54b5059-…` — no OKR scope
- Cindy `d8a77ff8-…` — no OKR scope

Target participation: ≥80% (≥13 of 16) by Friday 17:00.

## Filled template (parent issue body)

```markdown
## OKR Weekly Standup — Week {iso_week} (ISO {iso_year})

> Reporting window: {mon} 09:30 → {fri_date} 17:00 (Asia/Shanghai)
> Generated: {generation_timestamp} by routine `ef3be54d-…` (revision 3)
> Numbering scheme: ISO 8601 (Mon–Sun, week 1 = week containing first Thursday)
> Compute: `datetime.date(mon).isocalendar()` → `(iso_year, iso_week, 1)`

### Roster (16 reporters expected)

- [ ] **Lisa** (CEO)
- [ ] **CTO**
- [ ] **CPO**
- [ ] **Creative Director** (CSO)
- [ ] **Nuclear Domain Expert** (Research Director)
- [ ] **Lead Engineer** (LSE)
- [ ] **Skills Architect**
- [ ] **Workflow Designer**
- [ ] **SRE Monitor**
- [ ] **Release Engineer**
- [ ] **Code Reviewer**
- [ ] **E2E QA Tester**
- [ ] **UXDesigner**
- [ ] **Research Lead**
- [ ] **Dr. Ingrid Novak** (Optimization)
- [ ] **Dr. Alexander Petrov** (ML)

Each reporter's individual filing task is in a child issue assigned to them.
```

## Per-responder template (also the child issue body)

```markdown
## {Name} — Standup Week {iso_week}

### OKR Progress
- **KR-{ROLE}-{NUMBER}**: {current_value} / {target} — {{🟢 Green | 🟡 Yellow | 🔴 Red}}
- …repeat per active KR

### Completed This Week
- [NFM-XXX](https://paperclip/NFM/issues/NFM-XXX) — {one-line summary}
- …repeat

### Planned Next Week
- {key items}

### Blockers
- {if any}
```

## Hollow-filing gate (NFM-2454 — detected on Week 32)

A child issue whose description is **≤ 1000 characters** is a **hollow filing**: the assignee
checked out and closed `done` without filling the template. The per-responder template body
itself is ~290 chars; a real filing is always >3.8k chars after substitution.

**Before counting a child as "filed" in the close-out, apply this mechanical check:**

1. `GET /api/companies/{cid}/issues/{childId}` — read the `description` field.
2. If `len(description) <= 1000` → the child is **hollow**. It does NOT count as filed.
3. List hollow children by identifier in the synthesis comment under a new `### Hollow filings` section.

This gate prevents the board from reading "15/16 done" when the real number is 6/16. It was
empirically validated on Week 32: 9 of 16 children were `done` with 292–310 char descriptions.

## Close-out (Fri 17:00, owner: Strategy Director)

- **Apply the hollow-filing gate** (above) to every child before counting.
- Roll all 16 child-issue reports into one synthesis comment on the parent.
- Set parent status (counts use **filed** = substantive filings only, not raw `done` count):
  - **≥13 of 16 filed** → `done` (target met)
  - **8–12 filed** → `in_review` with per-reporter gap list and CEO/CSO mention
  - **<8 filed** → `blocked` with full gap list and explicit `@CEO` mention
- **Hard stop: Saturday 00:00.** Post the synthesis and final disposition regardless of coverage. Never let the issue rot past Friday.

## Synthesis comment format

```markdown
## Cycle {iso_week} synthesis — {filed}/{expected} ({pct}%)

### Coverage
- ✅ Filed: {names}
- ❌ Missing: {names}

### Hollow filings (closed `done` but template unmodified — do NOT count as filed)
- {child identifier} — {reporter name} ({len(description)} chars)

### Cross-cutting themes
- {themes extracted from filed reports}

### Escalations
- {blockers / red KRs that need CEO/CSO attention}
```
