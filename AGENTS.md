<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **nucpot** (12329 symbols, 17380 relationships, 192 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/nucpot/context` | Codebase overview, check index freshness |
| `gitnexus://repo/nucpot/clusters` | All functional areas |
| `gitnexus://repo/nucpot/processes` | All execution flows |
| `gitnexus://repo/nucpot/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Code Integrity Rules — Hermes × Paperclip Dual-Agent Collaboration (CRITICAL)

> **This section applies to ALL agents working on this repository, including Hermes (Etoile04) and Paperclip coding agents.**

### Branch Discipline

1. **main branch is READ-ONLY** — All changes MUST go through Pull Requests. Direct push to main is blocked by branch protection.
2. **Before starting ANY coding task**, run `git pull origin main` to sync with the latest code.
3. **Branch naming**: `feat/nfm-<issue>-<desc>` or `fix/nfm-<issue>-<desc>`
4. **Never force-push** to any branch that has an open PR.

### Multi-Agent Coordination

This repository has **two development teams** working simultaneously:

| Team | Commit Author | Identifier |
|------|--------------|-------------|
| **Hermes** | `Etoile04` | AI agent via Hermes Agent |
| **Paperclip** | `WenjieLi` (merge) | AI agents via Paperclip platform |

### SYNC Issues (MUST READ)

When you see a Paperclip issue with title prefixed `[SYNC]`, you MUST:

1. Read the full issue body — it contains a blast radius analysis of changes made by the other team
2. Check if any files listed overlap with your current task
3. If overlap exists: `git pull origin main` immediately and rebase your branch
4. Do NOT close or cancel SYNC issues — they are auto-generated coordination signals

### Before Editing (Checklist)

- [ ] `git pull origin main` — synced to latest
- [ ] `gitnexus detect_changes` — aware of recent changes by other agents
- [ ] `gitnexus impact <symbol>` — blast radius of your planned change
- [ ] No HIGH/CRITICAL risk conflicts with other team's recent changes

### Known Protected Files

These files have special protection rules:

- **`apps/web/src/app/api/verify/route.ts`** — Contains Hermes Completion Gate middleware. Do NOT remove the PC-2/PC-4 compliance checks without explicit approval from both teams.

<!-- code-integrity:end -->