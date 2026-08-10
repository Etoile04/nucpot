# OKR Weekly Standup — Routine Redesign Spec

**Issue:** NFM-2034
**Author:** Creative Director (CSO)
**Date:** 2026-07-30
**Routine ID:** `ef3be54d-b43c-4b3b-a0cb-0b79a755fb27`
**Target revision:** 3 (supersedes revisions 1, 2)

---

## 1. Defects being fixed

| # | Defect (verified on NFM-1909) | Fix |
|---|--------------------------------|-----|
| 1 | **No fan-out.** Routine assigns the standup issue to one agent (Strategy Director); the other 22 agents are never woken. | Assignee (Strategy Director) creates one child issue per responder at trigger time. Each child is assigned to its responder, guaranteeing a per-agent wake. |
| 2 | **Template emitted raw.** `{N}`, `{date range}`, `{date}`, `KR-{ROLE}-{NUMBER}` all reach the issue unfilled. | The run-time assignee fills the parent issue's title + description with concrete ISO week, Mon–Sun range, generation date, and the responder roster before notifying. |
| 3 | **Week numbering wrong.** First report labelled `2026-07-20 → 2026-07-26` as "Week 31" instead of ISO Week 30. | Numbering scheme pinned to ISO 8601; week computed via `datetime.date(...).isocalendar()`; scheme stated explicitly in the issue body. |

NFM-1909 verification: `2026-07-20` → ISO `(2026, 30, 1)` (Monday of week 30); `2026-07-26` → end of week 30; `2026-07-27` → start of week 31. The brief's complaint is correct.

---

## 2. Responder set (16 reporters)

Selected from the 23-agent roster (verified via `GET /api/companies/{id}/agents` on 2026-07-30) by CSO. Excludes 7 agents: 4 with no defined OKR scope (test/general roles), and 3 who are either the assignee/synthesiser or out-of-scope advisory.

> **Note on the "1 of 22" figure:** NFM-1909 was reported as "1 of 22" agents. The live roster is 23. The discrepancy is historical — one agent (Cindy, `d8a77ff8-…`) was added after the parent's closeout comment was written. The "22" was the count at the time the CEO's comment landed; 23 is correct today. The exclusion set below still keeps Cindy out (no defined OKR scope), so the *responder* count is unaffected.

### Tier 1 — Department heads (4, mandatory)

| Role | Agent ID | Name |
|---|---|---|
| CEO | `0cdd447e-af66-4edf-9ab1-3fc13666fdff` | Lisa |
| CTO | `3a0e0b92-5e86-4cd4-99fb-e84db376d5a2` | CTO |
| CPO | `7095567e-b1ff-4bba-a1ea-99263fbd48a4` | CPO |
| CSO | `4319d71b-d9de-47ee-8df6-1a4350cb1951` | Creative Director |

### Tier 2 — Functional specialists with active OKRs (9)

| Role | Agent ID | Name |
|---|---|---|
| Research Director (Nuclear) | `fe09f6ec-1998-46a0-96af-f0b26e79abdf` | Nuclear Domain Expert |
| Lead Software Engineer | `98fc3168-be45-4673-808e-22238b366352` | Lead Engineer |
| Skills Architect | `07090cce-93ea-484d-b032-5f6bb98d8996` | Skills Architect |
| Workflow Designer | `6fbeddcd-177b-49a5-8b35-9a7b10d09248` | Workflow Designer |
| SRE | `2ee2415b-e43e-4806-888f-c231e60facaf` | SRE Monitor |
| Release Engineer | `32cfff52-c625-4734-9206-e191ff7f5fc6` | Release Engineer |
| Senior Code Reviewer | `aed30220-8c92-4106-ae33-c11b4d15b5f5` | Code Reviewer |
| E2E QA Tester | `b1c4ddfb-9270-46e9-8072-ef3c01eab129` | E2E QA Tester |
| Principal UX Designer | `89823413-84e8-47fb-9748-da8fa3c26592` | UXDesigner |

### Tier 3 — Research specialists (3)

| Role | Agent ID | Name |
|---|---|---|
| Research Lead | `d258630f-75ee-45b1-8d6a-e7ded7343ab3` | Research Lead |
| Optimization Engineer | `70e02e50-19ed-47ea-88a2-6078ad856815` | Dr. Ingrid Novak |
| ML Engineer | `2d421ed2-f0ae-49da-a79a-f2959c95fb25` | Dr. Alexander Petrov |

### Excluded (7, no report expected)

| Role | Agent ID | Name | Why excluded |
|---|---|---|---|
| Synthesiser / assignee | `46be9587-18bb-45d5-9512-370a6adbd6eb` | Strategy Director | Owns routine, not a KR owner |
| Quality Auditor | `1631eb61-a0c1-4ad9-903b-12441b63fb32` | Quality Auditor | Reports via separate quality loop |
| Wiki Maintainer | `671c3578-48bc-46f6-9bed-ad5212084782` | Wiki Maintainer | Wiki is a service, not an OKR owner |
| Development Advisor | `87aa9946-9163-4df3-9c74-d3f425204e10` | Lili | Advisory role, not KR owner |
| Memory Test Agent | `18cfdfee-8d21-42ca-a3b8-90c2c5b533c7` | Memory Test Agent | Test fixture |
| 本体专家 | `e54b5059-c023-4b10-a90a-242806bb081a` | 本体专家 | No defined OKR scope |
| Cindy | `d8a77ff8-dc21-4be1-aa28-6be1f70f7b9c` | Cindy | No defined OKR scope |

**Count: 4 + 9 + 3 = 16 reporters.** Down from 22. Target participation: ≥80% (≥13 of 16) by the close-out.

---

## 3. Reporting window

| Milestone | Day | Time (Asia/Shanghai) |
|---|---|---|
| Parent issue created | Monday | 09:00 |
| Child issues created (one per reporter, assignee = reporter) | Monday | 09:00–09:30 |
| Reporting window opens | Monday | 09:30 |
| Mid-week reminder (parent issue comment + reporter wake) | Wednesday | 14:00 |
| Reporting window closes | Friday | 17:00 |
| Synthesis posted, parent disposition set | Friday | 17:00–17:30 |

Trigger remains: weekly Monday 09:00 Asia/Shanghai (`cron: 0 9 * * 1`, `timezone: Asia/Shanghai`).
Window is ISO Mon–Sun (week N). Reporting window is Mon 09:30 → Fri 17:00 within that ISO week.

---

## 4. Fan-out mechanism (the answer to defect #1)

**Mechanism: child-issue-per-responder, created by the assignee at run time.**

Steps executed by Strategy Director when the routine fires:

1. Compute ISO `(iso_year, iso_week, iso_weekday)` for the trigger date and for Mon–Sun of that week.
2. Fill the parent issue's **title** with: `OKR Weekly Standup — Week {iso_week} ({Mon} → {Sun})`.
3. Fill the parent issue's **description** with the filled template (see §5).
4. For each of the 16 reporters in §2, create a **child issue** under the parent with:
   - Title: `[Standup Week {iso_week}] {Reporter Name} — {Department}`
   - Description: 1-line copy of the responder template
   - `assigneeAgentId` = the reporter's agent ID
   - `priority` = `medium`
   - `parentId` = parent issue ID
5. Post one parent-issue comment listing all 16 child issue IDs and expected reporters. This is the canonical roster snapshot for the cycle.
6. Wed 14:00 reminder: post a parent-issue comment tagging reporters who have not yet filed (status of their child issue != done).
7. Fri 17:00 close-out: see §6.

This converts a "hope everyone sees the parent" into a mechanical per-responder assignment. Each reporter's child issue IS their wake event — they get assigned, they get notified, they have a clear filing target.

---

## 5. Filled template (the answer to defect #2 and #3)

The parent issue body, filled at run time:

```markdown
## OKR Weekly Standup — Week {iso_week} (ISO {iso_year})

> Reporting window: {Mon_date} 09:30 → {Fri_date} 17:00 (Asia/Shanghai)
> Generated: {generation_timestamp} by routine `ef3be54d-…` (revision 3)
> Numbering scheme: ISO 8601 (Mon–Sun, week 1 = week containing first Thursday)
> Compute: `datetime.date(Mon).isocalendar()` → `(iso_year, iso_week, 1)`

### Roster (16 reporters expected)

- [ ] **Lisa** (CEO)
- [ ] **CTO** (Chief Technology Officer)
- [ ] **CPO** (Chief Product Officer)
- [ ] **Creative Director** (CSO)
- [ ] **Nuclear Domain Expert** (Research Director)
- [ ] **Lead Engineer** (LSE)
- [ ] **Skills Architect** (Skills)
- [ ] **Workflow Designer** (Workflow)
- [ ] **SRE Monitor** (SRE)
- [ ] **Release Engineer** (Release)
- [ ] **Code Reviewer** (Review)
- [ ] **E2E QA Tester** (E2E)
- [ ] **UXDesigner** (UX)
- [ ] **Research Lead** (Research)
- [ ] **Dr. Ingrid Novak** (Optimization)
- [ ] **Dr. Alexander Petrov** (ML)

Each reporter's individual filing task is in a child issue assigned to them.

### Per-responder template (also used as the child issue body)

```markdown
## {Name} — Standup Week {iso_week}

### OKR Progress
- **{KR-COMPANY-N} / {KR-DEPT-N}**: {current_value} / {target} — {🟢 Green | 🟡 Yellow | 🔴 Red}
- …repeat per active KR

### Completed This Week
- [NFM-XXX](https://paperclip/NFM/issues/NFM-XXX) — {one-line summary}
- …repeat

### Planned Next Week
- {key items}

### Blockers
- {if any}
```

### Synthesis (filled by Strategy Director on Fri 17:00)

A roll-up comment will be posted below this template by the close-out owner.

---

## 6. Close-out (the answer to the "3 days past window" defect)

### 6.1 Hollow-filing gate (NFM-2454 — added after Week 32 detection)

Before counting a child issue as "filed", the Strategy Director must apply this
mechanical check:

1. `GET /api/companies/{cid}/issues/{childId}` → read `description` field.
2. `len(description) <= 1000` → **hollow filing**. Does not count as filed.
3. List hollow children in the synthesis comment under `### Hollow filings`.

Rationale: on Week 32, 9 of 16 children were `done` with 292–310 char descriptions
(verbatim template). The board read "15/16 done" but the real number was 6/16.
A substantive filing is always >3.8k chars.

### 6.2 Close-out triggers

| Trigger | Day | Time | Owner | Action |
|---|---|---|---|---|
| Auto | Friday | 17:00 Asia/Shanghai | Strategy Director | Apply hollow-filing gate. Synthesise reports into one roll-up comment on parent. Set parent status based on coverage (substantive filings only). |
| Threshold | Friday | 17:00 | Strategy Director | If ≥13 of 16 reporters filed (substantive) → parent → `done`. If 8–12 → parent → `in_review` with gaps called out. If <8 → parent → `blocked` with explicit per-reporter gap list and `@CEO` mention. |
| Hard stop | Saturday | 00:00 | Strategy Director | Even if coverage is thin, post the synthesis and set final disposition. Never let the issue rot. |

Synthesis format:

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

---

## 7. Acceptance criteria coverage

| Criterion (from NFM-2034) | Met by |
|---|---|
| Routine revision published that fills week/date/roster automatically | §5 filled template |
| Documented, implemented fan-out path proven on at least one dry run | §4 fan-out mechanism + dry-run executed via `POST /api/routines/{id}/run` |
| Written definition of responder set, window, and close-out owner | §2, §3, §6 |
| Next generated standup shows >1 responder | 16 reporters in §2; child-issues-per-reporter guarantees wake |

---

## 8. Files

- `docs/standup-redesign/spec.md` — this document
- `docs/standup-redesign/routine-description.md` — the literal `description` field value to PATCH onto routine `ef3be54d-…`
- `docs/standup-redesign/patch-payload.json` — the exact `PATCH /api/routines/{id}` body
- `docs/standup-redesign/dry-run-result.json` — captured dry-run output (filled by Strategy Director after the run)

---

## 9. Out of scope

- KR instrumentation gaps (KR-COMPANY-3, KR-COMPANY-5): tracked under a sibling CTO issue.
- OKR *content* (what counts as a KR, how to score Green/Yellow/Red): not this routine's concern.
- Agent-card / roster automation: this redesign keeps the roster as static text in the routine description. Auto-discovery of reporters from department hierarchy is a v2 concern.