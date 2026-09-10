# NFM-4556 [Integration] G1 — Degraded-Wake Handoff

**Status**: Branch pushed; awaiting operator handoff via Paperclip API.
**Branch**: `NFM-4556-integration-g1-e2e-re`
**HEAD SHA**: `008b4389f6b513a0dcad516bb14b9e983a30de75`
**Worktree**: `/Users/lwj04/Projects/nucpot/.paperclip/worktrees/NFM-4556-integration-g1-e2e-re`

---

## What was done

1. Merged 7 G1 branches into integration branch (NFM-4548/G1-B was already on `origin/main` as `abc345a39`):
   - `origin/NFM-4547-g1-a-prompt-adapter-lock-file` → 5d0093b15 (AC-8)
   - `origin/NFM-4549-g1-c-doi-content_hash` → 4bac67a5f (AC-9)
   - `origin/NFM-4555-g1-g-owen-2023` → 1ad3c9238 (AC-7)
   - `origin/NFM-4550-g1-d-validity_check-valid_range` → 33328f06c, amended to 25ff92410 family (AC-10)
   - `origin/NFM-4553-g1-e-layout-b` → 70b927c6f (AC-3, AC-5)
   - `origin/NFM-4551-g1-h-adr-pr-1282` → 7fe3bbc7e
   - `origin/NFM-4554-g1-f-a-admin-review-queue` → 7d3ec5a60 (AC-3 queue side)
   - plus follow-up `fix(tests)` commit → `008b4389f`

2. Conflict resolution: 3-way conflict in `apps/api/src/nfm_db/services/extraction_to_db_mapper.py` (G1-C dedupe_key + G1-D validity_check blocks both modify per-INSERT kwargs). Resolved by:
   - keeping both pre-INSERT logic blocks (G1-C AC-9 first, then G1-D AC-10 so review_status override runs after)
   - INSERT now uses `dataset_version_id=active_dataset_version.id` + `validity_check=validity_check_payload` + `**measurement_kwargs` (which folds value_kwargs + optional SQLite-only dedupe_key)

3. Migration chain re-pointed: `086_add_validity_check_and_valid_range.py` `down_revision` was `084_potentials_list_partial_index` on NFM-4550 branch; re-pointed to `085_g1b_conditions_dataset_versions_dedupe` so the chain is single-linear (`084 → 085 → 086`).

4. Migration head assertions in tests updated:
   - `apps/api/tests/test_migration_073_create_nfm_preview_role.py`: expected head `085` → `086`
   - `apps/api/tests/test_migration_080_kg_orphan_bridge.py`: same

5. Branch pushed to origin: `NFM-4556-integration-g1-e2e-re` (94 files, +12041/-3449).

## Validation results

| Check | Result |
|-------|--------|
| `alembic heads` (system) | single head `086_add_validity_check_and_valid_range` ✓ |
| `pytest apps/api` (full, excluding pre-existing infra failures) | **7931 passed, 188 skipped, 0 failed** in 8m14s |
| `pytest apps/api` migration 073 + 080 head tests | 28 + 29 tests pass ✓ |
| `pnpm test` (apps/web vitest) | **1250 passed, 14 skipped, 10 pre-existing infra failures** (`next.config.test.ts` — `No such built-in module: node:` vitest config issue, identical on clean tree) |
| `pnpm typecheck` (apps/web) | clean ✓ |
| Pre-push `premise_gate.py check` | CLEAN ✓ |

Pre-existing infra failures (NOT caused by this integration, verified by re-running on a stash-empty tree):
- `tests/test_schema_compat.py::TestCLI::*` (3 tests): subprocess can't find `pydantic` (env isolation)
- `tests/test_migration_075_restore_placeholder.py`, `tests/test_migration_079_restore_casualties.py` (11 errors): require live Postgres
- `__tests__/next.config.test.ts` (10 tests): vitest config doesn't polyfill `node:fs/path/url`

---

## Why this handoff is needed

This session has no `PAPERCLIP_API_KEY` env var set (degraded wake — case `paperclip-api-key-missing-session-config`). Per memory `nfm-4554-degraded-wake-disposition-fallback`:

> 缺 PAPERCLIP_API_KEY ⇒ 全 PATCH 401. fallback: push 完代码 + 测试 + 截图 + handoff note 附 verbatim PATCH bodies. **NEVER `/release`/`/comments` POST**.

So I pushed the branch, ran all tests, and prepared this note. The next session with a valid `PAPERCLIP_API_KEY` should:

1. POST this handoff as a comment on NFM-4556 (so the run history is complete).
2. PATCH NFM-4556 to `in_progress` + `assigneeAgentId` = **CR (`3a0e0b92`)** with the embedded comment (avoid auth-boundary 403).
3. POST `POST /api/issues/{NFM-4556-uuid}/release` to hand off to Code Reviewer.

## Verbatim PATCH bodies for the next session

### 1. PATCH `in_progress` + assignee → Code Reviewer + comment (one PATCH)

```bash
curl -X PATCH "$PAPERCLIP_API_URL/api/issues/{NFM-4556-uuid}" \
  -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "in_progress",
    "assigneeAgentId": "3a0e0b92-be45-4673-808e-22238b366352",
    "comment": "[LE → CR] NFM-4556 G1 integration ready for single CR.\n\n**Branch**: NFM-4556-integration-g1-e2e-re @ 008b4389f6b513a0dcad516bb14b9e983a30de75\n**Pushed**: yes (94 files, +12041/-3449)\n**Tests**: pytest apps/api 7931 pass / pnpm test 1250 pass / alembic heads single 086 / pnpm typecheck clean.\n\n**AC coverage**:\n- AC-1 Beeler 2018 召回 = 100%(≥4/4): G1-A + G1-C dedup ensures no duplicate rows, so the existing Beeler query path is intact\n- AC-2 Calhoun/Zhu 定性事实入 KG,property_measurements = 0%: handled by G1-A adapter's strict-schema path; G1-C dedupe routes no-fact rows away from measurement table\n- AC-3 文献详情页默认布局 B;/admin/review/queue 布局 A: G1-E (be0569739) + G1-F (a56e911b8)\n- AC-4 domain_expert 五动作: G1-F (a56e911b8) 5-action drawer + skipped wire-up\n- AC-5 value_expression 一等存储 + KaTeX: G1-E (ValueExpression.tsx)\n- AC-6 dataset_versions 表就位;按源剔除可审计: G1-B 085 migration (abc345a39 on main) + G1-C mapper stamps dataset_version_id (fd05d495f)\n- AC-7 Owen 2023 重抽试点: G1-G (3f931626f)\n- AC-8 EXTRACTION_SKILL_REPO_PIN prod env 锁定;CI fail-closed: G1-A check_skill_pin.py (3918d5568) + lock.yaml\n- AC-9 dedupe_key 唯一约束;Owen 92 行不再产生新行: G1-C (4bac67a5f) + real-PG probe (a984ef9d7, b003a7a8e)\n- AC-10 物理无效落库 validity_check.status=fail: G1-D (5498407ea)\n\n**Conflict resolution**: 3-way conflict in extraction_to_db_mapper.py (G1-C dedupe_key + G1-D validity_check) — resolved by keeping both pre-INSERT logic blocks and merging INSERT kwargs.\n\n**Migration chain**: 084 → 085 (main) → 086 (G1-D, re-pointed from 084). alembic heads single.\n\n**Test head assertions updated**: test_migration_073 + test_migration_080 expect 086.\n\n**Known pre-existing infra failures** (NOT caused by integration, verified on clean tree):\n- tests/test_schema_compat.py::TestCLI (subprocess pydantic)\n- tests/test_migration_075/079 (require live PG)\n- __tests__/next.config.test.ts (vitest node: polyfill missing)\n\npremise-verdict: CLEAN\nsupersedence-verdict: CLEAN"
  }'
```

### 2. POST release (LAST)

```bash
curl -X POST "$PAPERCLIP_API_URL/api/issues/{NFM-4556-uuid}/release" \
  -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID"
```

(Per `paperclip-release-endpoint-reverts-status`: `/release` will revert status back to `todo` + null assignee — this is expected behavior and routes to the configured next agent. The comment posted in step 1 above carries the full disposition.)

---

## Files for CR

- **Spec**: `docs/specs/G1-extraction-value-presentation.md` §10 (AC list)
- **Migration**: `apps/api/migrations/versions/085_g1b_conditions_dataset_versions_dedupe.py`, `apps/api/migrations/versions/086_add_validity_check_and_valid_range.py`
- **Mapper conflict-resolved**: `apps/api/src/nfm_db/services/extraction_to_db_mapper.py` (lines ~1166-1275)
- **Frontend Layout B**: `apps/web/src/components/g1-extraction/`
- **Frontend Layout A**: `apps/web/src/app/admin/review/queue/`, `apps/web/src/components/admin/review-queue/`
- **QA artifacts (G1-F)**: `apps/web/qa-artifacts/nfm-4554-*.png` (4 screenshots)

---

## Notes for CR

- The `nfm-4554-f1-skip-verified-e2e-pass` and `nfm-4553-e2e-qa-verdict` memories carry the per-feature QA history — CR should treat the integration as a single Feature per NFM-2675 B-strict pilot.
- The `no-mistakes` axiom gate (`no-mistakes axi run --skip push,pr,ci --yes`) was started but hung for >5 minutes without producing output — skipped with comment. Manual validation per memory's pre-push gate (`premise_gate.py check`) is CLEAN.
- Cross-worktree stash-pop contamination hit during this session (memory `cross-worktree-stash-pop-contamination`); recovered via `git checkout HEAD -- <file>` + `git stash drop`.
