#!/usr/bin/env bash
#
# CI guard: fail the build if `apps/api/src/` contains bare `except: pass`
# patterns that swallow errors without logging, alerting, or metrics.
#
# See NFM-2219 / NFM-2211 for the originating Sprint Gap-2/3 finding.
#
# Behaviour:
#   * Walks every `*.py` under apps/api/src/ and detects two patterns:
#       - single-line:   `except <errors>: pass`  (everything on one line)
#       - multi-line:    `except <errors>:\n[indent] pass`  (pass on next line)
#   * Files whose relative path is listed (uncommented) in the allowlist file
#     are skipped. Each allowlist line MUST be `# <path>: <reason>`.
#   * Exit 0 when zero violations remain, exit 1 with a precise report otherwise.
#
# Why a Python helper instead of pure grep:
#   GNU `grep -E "except.*:\s*pass"` matches the SINGLE-line variant only.
#   Catching the multi-line variant (the one that actually appears in this
#   repo today) requires either `grep -Pz` (over-matches with greedy `.*`)
#   or a small Python AST/regex pass. We use Python so the diagnostic output
#   is stable and the regex is easy to audit in code review.
#
# Performance: a full walk of apps/api/src/ runs in well under 10 s on CI.

set -euo pipefail

TARGET="${CHECK_EXCEPT_PASS_TARGET:-apps/api/src}"
ALLOWLIST_PATH="${CHECK_EXCEPT_PASS_ALLOWLIST:-.ci_except_pass_allowlist}"

# Resolve script directory so the script can run from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

if [[ ! -d "$TARGET" ]]; then
  echo "::error::check_except_pass: target directory '$TARGET' not found (cwd: $(pwd))" >&2
  exit 2
fi

# Inline Python helper — single source of truth for the regexes.
python3 - "$TARGET" "$ALLOWLIST_PATH" <<'PY'
import os
import re
import sys

target, allowlist_path = sys.argv[1], sys.argv[2]

# Allowlist: lines starting with '#' are comments. Empty lines are ignored.
# Each non-comment line is the relative path (relative to repo root) that
# has been explicitly documented as a legitimate bare-except-pass site.
# An optional trailing `# reason: <text>` comment may follow the path.
allowlist = set()
if os.path.exists(allowlist_path):
    with open(allowlist_path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # Strip trailing inline justification (`# reason: ...`).
            entry = line.split("#", 1)[0].strip()
            if entry:
                allowlist.add(entry)

# SINGLE: `except <errors>: pass` on a single physical line.
# `:` must be the only colon immediately before `pass`, so we exclude
# chained logic like `except A: x(); pass` (still flagged) and avoid the
# false positive `except as exc:` followed by non-pass code.
single_re = re.compile(r"except[^\n]*:[ \t]*pass\b")

# MULTI: `except <errors>:\n[indent] pass` — pass on the next line.
# Anchored so `except:\n    logger.debug(...)` does NOT match.
multi_re = re.compile(r"except[^\n]*:\n[ \t]*pass\b")

violations: list[tuple[str, int, str]] = []

for root, _dirs, files in os.walk(target):
    for name in sorted(files):
        if not name.endswith(".py"):
            continue
        abs_path = os.path.join(root, name)
        rel_path = os.path.relpath(abs_path)
        if rel_path in allowlist:
            continue
        with open(abs_path, encoding="utf-8") as fh:
            src = fh.read()
        for matcher, kind in ((single_re, "single"), (multi_re, "multi")):
            for m in matcher.finditer(src):
                line_no = src.count("\n", 0, m.start()) + 1
                snippet = m.group().splitlines()[0].strip()
                violations.append((rel_path, line_no, f"{kind}: {snippet[:120]}"))

if violations:
    print(
        "::error::CI guard failed: bare `except: pass` patterns detected in "
        f"{target} ({len(violations)} match{'es' if len(violations) != 1 else ''}).",
        file=sys.stderr,
    )
    print(
        "Each bare `except: pass` silently swallows errors. Replace `pass` "
        "with explicit logging (or re-raise). If a swallow is genuinely "
        "defensible, document it in .ci_except_pass_allowlist with a "
        "comment explaining why.", file=sys.stderr,
    )
    print("", file=sys.stderr)
    for rel_path, line_no, detail in sorted(violations):
        print(f"  {rel_path}:{line_no}: {detail}", file=sys.stderr)
    print("", file=sys.stderr)
    sys.exit(1)

print(f"OK: no bare `except: pass` patterns in {target} (allowlist size={len(allowlist)}).")
PY