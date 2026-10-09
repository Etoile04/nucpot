#!/usr/bin/env python3
"""check_prereg — dual-check pre-registration gate, Check 2 (scope guard).

Static, warn-only scanner that flags run artifacts whose scope *looks*
confirmatory but lack a pre-registration marker, routing them to NDE
sign-off instead of hard-failing review. Hard-fails only on an artifact
that explicitly labels itself ``[CONFIRMATORY-RUN]`` with no parseable
prereg block (NFM-5312 NDE review, recorded hard-fail boundary).

Authority chain (per NDE factual correction #1, NFM-5312 cad98897):
agent-profile working rule (ACP runtime AGENTS.md §Working Rules item 2)
+ NFM-5054 NDE ruling (2026-09-21) + NFM-5312 [PREREG-GATE-RECONFIRMED].
The repo-root AGENTS.md does NOT contain the pre-registration rule — do
not cite it for this gate.

Check 1 (process-side presence marker on the carrier issue, gated on
NDE's ``[PREREG-APPROVED]``) is unchanged and lives in the Paperclip
thread; this tool only guards artifacts. It never approves anything.

Usage::

    python3 scripts/check_prereg.py [PATHS ...]        # scan given paths
    python3 scripts/check_prereg.py --base REF         # scan git diff REF...HEAD
    python3 scripts/check_prereg.py --strict PATHS ... # any single weak signal triggers
    python3 scripts/check_prereg.py --verify PATHS ... # local-only: check the
                                                       # carrier thread for the
                                                       # NDE [PREREG-APPROVED]

Exit codes:
    0 — pass, or warnings only (warn never blocks)
    1 — hard-fail: [CONFIRMATORY-RUN] without a parseable block, or a
        --verify rejection on a confirmatory-labeled artifact
    2 — configuration error (no --base/PATHS, bad git range, missing
        Paperclip credentials under --verify)

Only stdlib is used; the offline path never touches the network.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Scan surface approved in NFM-5312 §4 (issue text) + NDE review.
# experiments/** is a reserved path (does not exist in this repo yet) kept
# so future run scripts are covered from day one — a glob matching nothing
# carries no coverage story (NDE correction #2): unit tests must exercise
# the surfaces that exist today.
SCAN_GLOBS: tuple[str, ...] = (
    "apps/api/src/nfm_db/ml/**",
    "apps/api/tests/**",
    "experiments/**",
    "docs/analysis/**",
    "docs/reports/**",
)

# Marker spec v1 (NFM-5312 §6): fenced ``prereg`` blocks, key: value lines.
FULL_REQUIRED_KEYS: tuple[str, ...] = (
    "carrier",
    "label",
    "objectives",
    "constraints",
    "algorithm",
    "seed",
    "convergence",
    "acceptance",
)

# NDE review Q2: minimal exploratory block is exactly these two keys.
MINIMAL_KEYS: frozenset[str] = frozenset({"label", "carrier"})
# NDE review Q4: short-form citing block is exactly this one key.
SHORT_KEYS: frozenset[str] = frozenset({"ref"})

PREREG_APPROVED_MARK = "[PREREG-APPROVED]"
# NDE (Nuclear Domain Expert) is the sole approval authority. NOTE the
# NFM-5305 precedent (discovered 2026-10-05 while calibrating --verify): the
# [PREREG-APPROVED] marker comment on a real carrier thread was authored by
# the run owner relaying NDE's gate verdict, not by NDE's own agent identity.
# Default --verify therefore accepts any agent-authored marker on the carrier
# thread; pass --approver <uuid> to enforce marker authorship strictly.
NDE_AGENT_ID = "fe09f6ec-1998-46a0-96af-f0b26e79abdf"

CONFIRMATORY_RUN_LABEL = "[CONFIRMATORY-RUN]"
CONFIRMATORY_LABEL_PREFIX = "[CONFIRMATORY"


class ConfigError(Exception):
    """Usage or environment error — maps to exit code 2."""


# ---------------------------------------------------------------------------
# Signal taxonomy (NFM-5312 §4). Strong signals trigger alone; weak signals
# trigger at >=2 (default) or >=1 under --strict (NDE review Q1).
# ---------------------------------------------------------------------------

SignalCheck = Callable[[str], bool]


def _has_proposal_reproduction_claim(text: str) -> bool:
    lowered = text.lower()
    return "申报书" in text and any(
        marker in lowered or marker in text for marker in ("reproduc", "复现", "最优成分")
    )


def _has_pop_gen_block(text: str) -> bool:
    """技术路线图 §5.3 canonical budget: population=200 x generations=100."""
    return bool(
        re.search(r"(?i)\bpopulation\s*[:=]\s*200\b", text)
        and re.search(r"(?i)\bgenerations?\s*[:=]\s*100\b", text)
    )


STRONG_SIGNALS: tuple[tuple[str, SignalCheck], ...] = (
    (
        "confirmatory-label",
        lambda t: bool(re.search(r"\[CONFIRMATORY(?:-RUN)?\]", t)),
    ),
    (
        "promotion-claim",
        lambda t: bool(re.search(r"(?i)promotion\s+run|dispatch-candidate", t)),
    ),
    ("proposal-reproduction", _has_proposal_reproduction_claim),
)

WEAK_SIGNALS: tuple[tuple[str, SignalCheck], ...] = (
    ("pinned-seed", lambda t: bool(re.search(r"(?i)\bseed\s*[:=]\s*\d+", t))),
    ("pop-gen-200x100", _has_pop_gen_block),
    (
        "pareto-front-dump",
        lambda t: bool(re.search(r"(?i)pareto", t) and re.search(r"(?i)front|non-dominated", t)),
    ),
    (
        "convergence-metrics",
        lambda t: bool(re.search(r"(?i)hypervolume|generational\s+distance", t)),
    ),
)

DEFAULT_WEAK_THRESHOLD = 2
STRICT_WEAK_THRESHOLD = 1


@dataclass(frozen=True)
class Signals:
    strong: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()

    @property
    def fired(self) -> tuple[str, ...]:
        return self.strong + self.weak

    def triggered(self, weak_threshold: int) -> bool:
        return bool(self.strong) or len(self.weak) >= weak_threshold


def classify_signals(text: str) -> Signals:
    strong = tuple(name for name, check in STRONG_SIGNALS if check(text))
    weak = tuple(name for name, check in WEAK_SIGNALS if check(text))
    return Signals(strong=strong, weak=weak)


# ---------------------------------------------------------------------------
# Block lexers — two lexers, one key set (NDE review, parser clarification).
# (a) fenced ```prereg blocks in markdown / docstrings
# (b) Python comment blocks: first line exactly "# prereg", then "# key: value"
# ---------------------------------------------------------------------------

FENCED_PREREG_RE = re.compile(
    r"^[ \t]*```prereg[ \t]*\n(.*?)^[ \t]*```[ \t]*$", re.DOTALL | re.MULTILINE
)
COMMENT_BLOCK_HEADER_RE = re.compile(r"^#[ \t]*prereg[ \t]*$")
COMMENT_KEYVAL_RE = re.compile(r"^#[ \t]*(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:[ \t]*(?P<value>.*)$")


def _parse_keyvals(raw_lines: list[str]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for line in raw_lines:
        if not line.strip():
            continue
        if ":" not in line:
            return {}  # malformed line invalidates the block
        key, _, value = line.partition(":")
        if not key.strip():
            return {}
        keys[key.strip().lower()] = value.strip()
    return keys


def parse_fenced_blocks(text: str) -> list[dict[str, str]]:
    return [_parse_keyvals(m.group(1).splitlines()) for m in FENCED_PREREG_RE.finditer(text)]


def parse_comment_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if current is not None:
            if line.lstrip().startswith("#"):
                match = COMMENT_KEYVAL_RE.match(line.strip())
                if match:
                    current = [*current, f"{match.group('key')}: {match.group('value')}"]
                    continue
            blocks.append(_parse_keyvals(current))
            current = None
        elif COMMENT_BLOCK_HEADER_RE.match(line.strip()):
            current = []
    if current is not None:
        blocks.append(_parse_keyvals(current))
    return blocks


VARIANT_FULL = "full"
VARIANT_SHORT = "short"
VARIANT_MINIMAL = "minimal"
VARIANT_INVALID = "invalid"


@dataclass(frozen=True)
class PreregBlock:
    variant: str
    keys: dict[str, str]

    @property
    def carrier(self) -> str:
        if self.variant == VARIANT_SHORT:
            return self.keys.get("ref", "")
        return self.keys.get("carrier", "")

    @property
    def label(self) -> str:
        return self.keys.get("label", "").upper()


def classify_block(keys: dict[str, str]) -> PreregBlock:
    """Short-form, minimal, and full variants satisfy presence; anything else
    (missing/empty required key for its variant) does not count."""
    if not keys:
        return PreregBlock(variant=VARIANT_INVALID, keys=keys)
    if set(keys) == SHORT_KEYS and keys.get("ref", ""):
        return PreregBlock(variant=VARIANT_SHORT, keys=keys)
    if (
        set(keys) == MINIMAL_KEYS
        and keys.get("carrier", "")
        and keys.get("label", "").upper() == "EXPLORATORY"
    ):
        return PreregBlock(variant=VARIANT_MINIMAL, keys=keys)
    if all(keys.get(key, "") for key in FULL_REQUIRED_KEYS):
        return PreregBlock(variant=VARIANT_FULL, keys=keys)
    return PreregBlock(variant=VARIANT_INVALID, keys=keys)


def parse_blocks(text: str) -> list[PreregBlock]:
    raw = parse_fenced_blocks(text) + parse_comment_blocks(text)
    return [classify_block(keys) for keys in raw]


def best_block(blocks: list[PreregBlock]) -> PreregBlock | None:
    """Full > short > minimal; invalid never satisfies presence. Returns the
    highest-ranked valid block, or None."""
    for variant in (VARIANT_FULL, VARIANT_SHORT, VARIANT_MINIMAL):
        for block in blocks:
            if block.variant == variant:
                return block
    return None


# ---------------------------------------------------------------------------
# File evaluation — the three outcome branches (NFM-5312 §4 outcomes table,
# hard-fail boundary as recorded by the NDE review).
# ---------------------------------------------------------------------------

OUTCOME_OK = "ok"
OUTCOME_WARN = "warn"
OUTCOME_FAIL = "fail"


@dataclass(frozen=True)
class Finding:
    path: str
    outcome: str
    reason: str
    signals: tuple[str, ...] = ()
    carrier: str = ""
    confirmatory_labeled: bool = False


def evaluate_text(relpath: str, text: str, weak_threshold: int) -> Finding:
    signals = classify_signals(text)
    confirmatory_labeled = CONFIRMATORY_LABEL_PREFIX in text
    if not signals.triggered(weak_threshold):
        return Finding(path=relpath, outcome=OUTCOME_OK, reason="no signals")
    block = best_block(parse_blocks(text))
    if block is None:
        if CONFIRMATORY_RUN_LABEL in text:
            return Finding(
                path=relpath,
                outcome=OUTCOME_FAIL,
                reason="[CONFIRMATORY-RUN] declared with no parseable prereg block",
                signals=signals.fired,
                confirmatory_labeled=True,
            )
        return Finding(
            path=relpath,
            outcome=OUTCOME_WARN,
            reason="may be confirmatory — NDE sign-off required before merge",
            signals=signals.fired,
        )
    # Q2 sharpening: a strong signal cannot be satisfied away by an
    # EXPLORATORY minimal block — automatic NDE routing.
    if signals.strong and block.variant == VARIANT_MINIMAL:
        return Finding(
            path=relpath,
            outcome=OUTCOME_WARN,
            reason="strong signal with only an EXPLORATORY minimal block — automatic NDE routing",
            signals=signals.fired,
            carrier=block.carrier,
            confirmatory_labeled=confirmatory_labeled,
        )
    return Finding(
        path=relpath,
        outcome=OUTCOME_OK,
        reason=f"prereg block satisfied ({block.variant}; carrier={block.carrier})",
        signals=signals.fired,
        carrier=block.carrier,
        confirmatory_labeled=confirmatory_labeled,
    )


# ---------------------------------------------------------------------------
# --verify (local-only, NDE review Q3): carrier-thread lookup of the NDE
# [PREREG-APPROVED] for confirmatory-labeled artifacts. Never runs in CI.
# ---------------------------------------------------------------------------


def has_approved_comment(comments: list[dict], approver_agent_id: str | None) -> bool:
    """Pure predicate over a carrier-thread comment list: an agent-authored
    ``[PREREG-APPROVED]`` marker, optionally restricted to one author."""
    return any(
        PREREG_APPROVED_MARK in (comment.get("body") or "")
        and comment.get("authorType") == "agent"
        and (approver_agent_id is None or comment.get("authorAgentId") == approver_agent_id)
        for comment in comments
    )


def fetch_carrier_approval(carrier_key: str, approver_agent_id: str | None) -> bool:
    """True iff the carrier issue thread carries an agent-authored
    [PREREG-APPROVED] marker (optionally by ``approver_agent_id``). Raises
    ConfigError when Paperclip is unreachable or credentials are missing;
    returns False for a resolvable carrier with no approval (that is a
    verify rejection, not a config problem)."""
    api_key = os.environ.get("PAPERCLIP_API_KEY")
    api_url = os.environ.get("PAPERCLIP_API_URL", "").rstrip("/")
    if not api_key or not api_url:
        raise ConfigError(
            "--verify needs PAPERCLIP_API_KEY and PAPERCLIP_API_URL in the environment"
        )

    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from paperclip_issue_lookup import ApiError, NotFound, Ok, lookup_issue

    result = lookup_issue(carrier_key)
    if isinstance(result, Ok):
        issue_uuid = result.issues[0]["id"]
    elif isinstance(result, NotFound):
        return False  # no carrier issue ⇒ no approval can exist ⇒ verify rejection
    elif isinstance(result, ApiError):
        raise ConfigError(f"carrier lookup failed for {carrier_key}: {result.kind}")
    else:  # discriminated union guard — never treat a lookup error as "no approval"
        raise ConfigError(f"carrier lookup failed for {carrier_key}: {result!r}")

    request = urllib.request.Request(
        f"{api_url}/api/issues/{issue_uuid}/comments",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            comments = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise ConfigError(f"carrier comment fetch failed for {carrier_key}: {exc}") from exc

    return has_approved_comment(comments, approver_agent_id)


def verify_confirmatory(findings: list[Finding], approver_agent_id: str) -> list[Finding]:
    """Turn confirmatory-labeled, marker-satisfied findings into FAILs when
    the carrier thread lacks the NDE approval."""
    extra: list[Finding] = []
    for finding in findings:
        if not (finding.confirmatory_labeled and finding.carrier):
            continue
        approved = fetch_carrier_approval(finding.carrier, approver_agent_id)
        if not approved:
            extra.append(
                Finding(
                    path=finding.path,
                    outcome=OUTCOME_FAIL,
                    reason=(
                        f"confirmatory-labeled but carrier {finding.carrier} has no "
                        f"{PREREG_APPROVED_MARK} by the approver agent"
                    ),
                    signals=finding.signals,
                    carrier=finding.carrier,
                    confirmatory_labeled=True,
                )
            )
    return extra


# ---------------------------------------------------------------------------
# Scanning drivers
# ---------------------------------------------------------------------------


def in_scan_surface(relpath: str) -> bool:
    return any(fnmatch.fnmatch(relpath, pattern) for pattern in SCAN_GLOBS)


def scan_texts(
    texts: dict[str, str],
    weak_threshold: int = DEFAULT_WEAK_THRESHOLD,
) -> list[Finding]:
    return [
        evaluate_text(relpath, text, weak_threshold)
        for relpath, text in sorted(texts.items())
        if in_scan_surface(relpath)
    ]


def collect_paths(root: Path, requested: list[str]) -> tuple[list[str], int]:
    """Resolve requested paths to repo-relative files inside the scan surface.
    Returns (relpaths, skipped_count) — non-surface paths are skipped silently
    by design (the surface, not the caller, decides scope)."""
    files: list[str] = []
    skipped = 0
    for name in requested:
        target = (root / name).resolve()
        if target.is_dir():
            candidates = sorted(p for p in target.rglob("*") if p.is_file())
        else:
            candidates = [target]
        for candidate in candidates:
            try:
                relpath = str(candidate.relative_to(root))
            except ValueError:
                skipped += 1
                continue
            if in_scan_surface(relpath):
                files.append(relpath)
            else:
                skipped += 1
    return files, skipped


def git_diff_paths(base: str) -> list[str]:
    try:
        output = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=d", f"{base}...HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise ConfigError(f"git diff --name-only {base}...HEAD failed: {exc.stderr.strip()}") from exc
    return [line.strip() for line in output.splitlines() if line.strip() and in_scan_surface(line.strip())]


def format_finding(finding: Finding) -> str:
    tag = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}[finding.outcome]
    signals = f" [signals: {','.join(finding.signals)}]" if finding.signals else ""
    return f"{tag} {finding.path}: {finding.reason}{signals}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="files/dirs to scan (repo-relative)")
    parser.add_argument("--base", help="scan git diff BASE...HEAD instead of explicit paths")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="any single weak signal triggers (NDE review burn-in mode)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="local-only: verify confirmatory-labeled artifacts against the carrier thread",
    )
    parser.add_argument(
        "--approver",
        default=None,
        metavar="AGENT_UUID",
        help=(
            "restrict --verify to [PREREG-APPROVED] markers authored by this agent id; "
            f"default accepts any agent-authored marker (NDE id: {NDE_AGENT_ID})"
        ),
    )
    args = parser.parse_args(argv)

    if args.base and args.paths:
        print("error: --base and explicit PATHS are mutually exclusive", file=sys.stderr)
        return 2
    if not args.base and not args.paths:
        print("error: give PATHS or --base (diff-scoped by design)", file=sys.stderr)
        return 2

    try:
        if args.base:
            relpaths = git_diff_paths(args.base)
            skipped = 0
        else:
            relpaths, skipped = collect_paths(REPO_ROOT, args.paths)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not relpaths:
        print(f"check_prereg: 0 in-surface files to scan ({skipped} skipped) — nothing to do")
        return 0

    texts: dict[str, str] = {}
    for relpath in relpaths:
        try:
            texts[relpath] = (REPO_ROOT / relpath).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            print(f"check_prereg: skip {relpath} (absent at HEAD — deleted or renamed away)")
            continue
        except OSError as exc:
            print(f"error: cannot read {relpath}: {exc}", file=sys.stderr)
            return 2

    threshold = STRICT_WEAK_THRESHOLD if args.strict else DEFAULT_WEAK_THRESHOLD
    findings = scan_texts(texts, weak_threshold=threshold)

    if args.verify:
        try:
            findings = [*findings, *verify_confirmatory(findings, args.approver)]
        except ConfigError as exc:
            print(f"error: --verify failed: {exc}", file=sys.stderr)
            return 2

    for finding in findings:
        print(format_finding(finding))

    fails = sum(1 for f in findings if f.outcome == OUTCOME_FAIL)
    warns = sum(1 for f in findings if f.outcome == OUTCOME_WARN)
    ok = len(findings) - fails - warns
    print(
        f"check_prereg: scanned {len(findings)} in-surface file(s) "
        f"({skipped} out-of-surface skipped): {ok} pass, {warns} warn, {fails} fail "
        f"[threshold: weak>={threshold}]"
    )
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
