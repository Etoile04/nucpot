# NFM-376 Code Quality + Security Review Report

**Issue**: NFM-369.7: 代码审查 + 安全审查
**Date**: 2026-06-23
**Reviewer**: Lead Engineer (parallel python-reviewer + security-reviewer agents)
**Branch**: `nfm-371-md-runner-scaffold`
**Verdict**: **BLOCK** — 4 CRITICAL + 10 HIGH issues must be fixed before merge

---

## Summary

Comprehensive code quality and security review of NFM-369 MD Runner codebase (8 source files, 8 API integration files, 10 test files). The codebase demonstrates good hexagonal architecture in business-logic modules and proper SQLAlchemy parameterized queries throughout. However, critical gaps exist in API authorization, SLURM command injection prevention, and several runtime-crash bugs.

---

## CRITICAL Issues (4)

### CQ1. CLI `fit-potential` crashes — wrong constructor kwarg
- **File**: `apps/nfm-md-runner/src/nfm_md_runner/cli.py:170`
- **Issue**: Calls `ModelFitter(fitting_method=method)` but `ModelFitter.__init__` takes `method`, not `fitting_method`. Raises `TypeError` on every invocation.
- **Fix**: Change to `ModelFitter(method=method)`

### CQ2. Tests call misaligned signatures (false confidence)
- **File**: `apps/nfm-md-runner/tests/test_model_fitter.py:102,116`
- **Issue**: `fit_potential` called with positional `bounds` that maps to `constraints` param; `validate_fitting` called with `FittingResult` but expects `dict[str, float]`. Tests pass only because mocks don't validate types.
- **Fix**: Align test signatures with implementation, or fix implementation to match tests.

### CQ3. Missing Alembic migration for `CANCELLED` job status
- **File**: `apps/api/src/nfm_db/models/md_verification.py:37,143`
- **Issue**: New `JobStatus.CANCELLED` enum + updated `CheckConstraint` added, but no Alembic migration. The `cancel_md_verification_job` endpoint sets status to `CANCELLED` which will fail at runtime against existing DB constraint.
- **Fix**: Add Alembic migration altering `check_md_job_status` constraint.

### CQ4. Async ORM `selectinload` on non-relationship attribute
- **File**: `apps/api/src/nfm_db/services/md_verification.py:1056`
- **Issue**: `selectinload(MDVerificationJob._sa_instance_state)` — `_sa_instance_state` is not a relationship. Will raise at query time. The method then N+1-queries related tables anyway, making the selectinload both broken and redundant.
- **Fix**: Remove the `.options(selectinload(...))` call.

### CS1. Missing authentication on `/api/v1/verification/*` endpoints
- **File**: `apps/api/src/nfm_db/api/v1/verification.py:192,263,325`
- **Issue**: Three endpoints (`check-gap`, `adjudicate-grade`, `quarterly-audit`) have **no `get_current_user` dependency**. Any unauthenticated caller can invoke these workflows.
- **Fix**: Add `current_user: User = Depends(get_current_user)` to all three endpoint signatures.

### CS2. SLURM script injection via unvalidated `nodes`/`ntasks_per_node`
- **File**: `apps/nfm-md-runner/src/nfm_md_runner/hpc_adapter.py:835-836,844-845`
- **Issue**: `partition` and `walltime` are validated with `validate_shell_safe()`, but `nodes` and `ntasks` from the API request `config` dict are interpolated raw into the SLURM batch script. A caller can inject shell commands via `config={"nodes": "1\n./malicious.sh\n#"}`.
- **Fix**: Validate `nodes`/`ntasks` as positive integers before interpolation.

---

## HIGH Issues (10)

### HQ1. `get_job_status` does not validate `job_id` — command injection
- **File**: `hpc_adapter.py:388,403-405`
- **Issue**: `cancel_job` validates `job_id` with regex, but `get_job_status` interpolates raw. API endpoint accepts `job_id` from path.
- **Fix**: Extract `_validate_job_id()` helper; call in all SLURM methods.

### HQ2. Silent exception swallowing in `_ensure_remote_directory`
- **File**: `hpc_adapter.py:658-659`
- **Issue**: `except Exception` swallows permission/auth errors; caller proceeds assuming directory exists.
- **Fix**: Narrow exception type, or re-raise after logging.

### HQ3. Global `settings = Settings()` at import time creates directories
- **File**: `config.py:117`
- **Issue**: `field_validator` with side effects (`mkdir`) couples import to filesystem mutation. Prevents dependency injection.
- **Fix**: Move directory creation to explicit `ensure_directories()` method.

### HQ4. `NameError` in `config.py` `__main__` block
- **File**: `config.py:163-166`
- **Issue**: References `current_settings` which is only defined inside `verify_environment()`.
- **Fix**: Construct `Settings()` in the `__main__` block.

### HQ5. `_connections` dict shared without thread safety
- **File**: `hpc_adapter.py:163-164,256-262`
- **Issue**: Concurrent Celery workers sharing a connection pool will corrupt the dict.
- **Fix**: Add `threading.Lock` or document single-threaded requirement.

### HQ6. No timeout on `squeue`/`sacct` command execution
- **File**: `hpc_adapter.py:454-463`
- **Issue**: Hung SLURM scheduler blocks Celery worker indefinitely.
- **Fix**: Use `channel.settimeout(...)`.

### HQ7. `Dict[str, any]` — lowercase `any` used instead of `Any` type
- **File**: `analysis_manager.py:48,50,102,199,217,219` (6 occurrences)
- **Issue**: `any` is the builtin function, not a type. mypy will reject.
- **Fix**: `from typing import Any` and replace.

### HQ8. `_serialize_results` breaks on numpy/datetime values
- **File**: `analysis_manager.py:217-234`
- **Issue**: `json.dump` raises `TypeError` on `np.ndarray` or `datetime`.
- **Fix**: Use `default=str` in `json.dump`.

### HQ9. IDOR — No ownership check on MD verification jobs
- **File**: `apps/api/src/nfm_db/api/v1/md_verification.py` (all endpoints)
- **Issue**: `current_user` is fetched but never used for filtering. Any authenticated user can read/delete any other user's jobs.
- **Fix**: Add `owner_id` to model, filter queries by `current_user.id`.

### HQ10. No rate limiting on MD verification submission
- **File**: `apps/api/src/nfm_db/api/v1/md_verification.py:112`
- **Issue**: `POST /jobs` dispatches SSH+SLURM operations with no rate limit. Cost/DoS vector on shared HPC.
- **Fix**: Apply existing `make_rate_limit_dependency` pattern.

### HS1. Sensitive error details leaked to clients
- **Files**: `md_verification.py` (9 locations), `verification.py` (3 locations)
- **Issue**: `f"Failed to ... : {e!s}"` in HTTP responses leaks internal paths, SQLAlchemy errors, SSH strings.
- **Fix**: Return generic messages to clients, log details server-side.

### HS2. Unvalidated file paths forwarded to HPC and local FS
- **File**: `md_verification.py:64-73`, `md_tasks.py:167-178`
- **Issue**: Arbitrary `potential_file`/`structure_file` paths accepted. Could read `/etc/passwd` or upload to HPC.
- **Fix**: Validate paths are under allow-listed uploads directory using `Path.resolve()`.

---

## MEDIUM Issues (8)

| # | File | Issue |
|---|------|-------|
| M1 | `hpc_adapter.py:36, hpc_file_transfer.py:19` | `validate_remote_path` allows `..` traversal |
| M2 | `hpc_file_transfer.py:262` | Walrus operator precedence bug in `get_remote_checksum` |
| M3 | `md_verification.py:79` | Mutable default `jobs: list[...] = []` |
| M4 | `md_tasks.py:228-237` | Non-idiomatic Celery Retry construction |
| M5 | `md_verification.py:64-73` | Schema fields lack `max_length` constraints |
| M6 | `hpc_adapter.py:799-808` | `_select_cluster` failover is unimplemented |
| M7 | `hpc_adapter.py` | File is 873 lines (exceeds 800-line guideline) |
| M8 | `analysis_manager.py:119,143` | `print()` in business logic instead of `logging` |

---

## LOW Issues (4)

| # | File | Issue |
|---|------|-------|
| L1 | `__init__.py:21-25` | Public exports don't include `AnalysisManager` or `HPCAdapter` |
| L2 | `test_hpc_adapter.py:619` | Class-level pytest marker may not apply in all versions |
| L3 | `defect_analyzer.py` | `ImportError` for missing OVITO prevents test construction |
| L4 | `config.py:162-168` | `print()` for CLI output instead of logging |

---

## Positive Observations

- **Hexagonal architecture**: Clean separation of business logic (`defect_analyzer`, `data_averager`, `model_fitter`) with zero SSH/DB coupling
- **Protocol interfaces**: `runtime_checkable` Protocols for `Fitter`, `Averager`, `DefectDetector` are well-defined ports
- **SSH security**: `RejectPolicy` default, key-permission check (600), no password auth, shell-metacharacter allowlist for SLURM script generation
- **SQL injection**: Async SQLAlchemy uses parameterized queries throughout — no raw SQL
- **Secrets**: `SecretStr` for sensitive fields, no hardcoded credentials, `.env.example` is clean
- **Test breadth**: Good coverage of CLI, adapters, managers, protocols, and edge cases

---

## Required Actions Before Merge

### Must Fix (BLOCK)

1. **CQ1**: Fix `ModelFitter(fitting_method=...)` → `ModelFitter(method=...)` in `cli.py:170`
2. **CS1**: Add auth to `verification.py` endpoints
3. **CS2**: Validate `nodes`/`ntasks` as integers in SLURM script generation
4. **CQ3**: Add Alembic migration for `JobStatus.CANCELLED`
5. **CQ4**: Remove broken `selectinload(_sa_instance_state)` in `md_verification.py:1056`
6. **HQ1**: Add `job_id` validation to `get_job_status` and `download_results`
7. **HQ9**: Add `owner_id` to `MDVerificationJob` model + filter by `current_user`
8. **HS2**: Allow-list file paths under uploads directory
9. **HS1**: Sanitize error messages (remove exception details from responses)

### Should Fix (same PR or fast-follow)

10. **HQ2**: Stop swallowing exceptions in `_ensure_remote_directory`
11. **HQ7**: Replace `any` with `Any` type (6 occurrences)
12. **HQ3**: Move directory creation out of `field_validator`
13. **HQ4**: Fix `NameError` in `config.py` `__main__`
14. **HQ6**: Add timeout to SLURM command execution
15. **HQ8**: Handle numpy/datetime in `_serialize_results`
16. **HQ10**: Rate-limit submission endpoint
17. **HQ5**: Thread-safe connection pool or documentation
