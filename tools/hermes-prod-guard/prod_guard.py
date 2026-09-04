"""NFM-4269 / ADR-013 G1+G3 — prod-compose mutation matcher (pure functions).

Scope (ADR-013 §2 G1, NFM-4269):

* BLOCK ``docker compose``/``docker-compose`` invocations whose subcommand is
  a mutation verb (up/down/build/rebuild/restart/stop/start/rm/kill/run/exec)
  when the invocation references a prod marker — ``docker-compose.prod.yml``,
  ``docker/.env.prod`` (any ``--env-file``/``-f`` spelling), or the prod
  project name ``nucpot-prod`` (``-p``/``--project-name``/``COMPOSE_PROJECT_NAME``).
  Markers are detected on the raw segment text AND on the shlex-unescaped
  words, so unquoted backslash-escape obfuscation
  (``docker-compose\\.prod\\.yml``) is caught (NFM-4284 N1); quoted-literal
  backslashes name a different file and correctly do not match.
* BLOCK bare ``docker stop|rm|restart|kill|exec`` targeting prod containers
  (``nucpot-prod*``), including the ``docker container <verb>`` spelling.
* BLOCK terminal-vector writes (redirect/append, ``tee``, ``sed -i``,
  ``cp``/``mv``/``install`` destination) to the prod compose/env files — the
  redirect check runs on EVERY segment regardless of the head command, so
  ``docker compose config > docker-compose.prod.yml`` is caught too.
* NEVER block read-only: ``docker ps/inspect/logs/stats``, ``cat``/``grep``
  of prod files, ``docker compose config`` render.
* Sanctioned carve-outs key on the canonical deploy-identity marker
  (NFM-4268 comment ``aecb57d3``, binding via NFM-4274): "sanctioned" =
  execution under a dedicated local deploy identity, acquired only via
  command-enumerated sudo at the sanctioned chokepoints (deploy_prod.sh,
  GH runner production-deployment.yml step, enumerated NFM-1664 recovery
  entries). No env-var markers (forgeable inline — explicitly rejected by
  ADR-013 G1). Under that definition the in-session sanctioned set is EMPTY
  — acquiring the identity requires a root-level, sudo-log-audited act
  outside a bare terminal command from an agent session — so there is no
  in-session carve-out mechanism here to forge: ``sudo -u <deploy-identity>``
  typed into an agent session is NOT a chokepoint and is blocked like any
  other wrapper, and ``deploy_prod.sh`` / the GH runner / NFM-1664 recovery
  simply never route through the Hermes terminal tool (structural).

Stdlib only at import time; Hermes modules are imported lazily by the plugin
wrapper so this file stays testable in the plain nucpot pytest environment.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "COMPOSE_MUTATION_VERBS",
    "DOCKER_CONTAINER_MUTATION_VERBS",
    "PROD_CONTAINER_PREFIX",
    "PROD_FILE_MARKERS",
    "PROD_PROJECT_NAME",
    "BlockVerdict",
    "evaluate_command",
    "evaluate_write_target",
    "is_prod_touching",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROD_FILE_MARKERS: Sequence[str] = (
    "docker-compose.prod.yml",
    ".env.prod",
)
PROD_PROJECT_NAME = "nucpot-prod"
PROD_CONTAINER_PREFIX = "nucpot-prod"

# ADR-013 G1 mutation verbs for `docker compose` invocations.
COMPOSE_MUTATION_VERBS = frozenset({
    "up", "down", "build", "rebuild", "restart", "stop", "start",
    "rm", "kill", "run", "exec",
})

# ADR-013 G1 bare-docker container verbs (prod containers only).
DOCKER_CONTAINER_MUTATION_VERBS = frozenset({
    "stop", "rm", "restart", "kill", "exec",
})

# Leading command wrappers that may precede docker/compose.
_WRAPPER_COMMANDS = frozenset({
    "sudo", "exec", "nohup", "setsid", "time", "command", "env",
    "timeout", "nice", "stdbuf",
})

# sudo flags that consume the NEXT word.
_SUDO_VALUE_FLAGS = frozenset({"-u", "--user", "-g", "--group", "-p", "--prompt", "-C"})

# env flags that consume the NEXT word.
_ENV_VALUE_FLAGS = frozenset({"-u", "--unset"})

# timeout flags that consume the NEXT word.
_TIMEOUT_VALUE_FLAGS = frozenset({
    "-k", "--kill-after", "-s", "--signal", "--kill-time",
})

# nice flags that consume the NEXT word.
_NICE_VALUE_FLAGS = frozenset({"-n", "--adjustment"})

# docker-level flags that consume the NEXT word (before the subcommand).
_DOCKER_VALUE_FLAGS = frozenset({
    "-h", "--host", "-l", "--log-level", "--context", "--config",
})

# docker-compose global flags that consume the NEXT word (before the
# subcommand). Both `docker compose` and legacy `docker-compose` accept these.
_COMPOSE_VALUE_FLAGS = frozenset({
    "-f", "--file", "--env-file", "-p", "--project-name", "--profile",
    "--context", "--project-directory", "--log-level", "--volume",
})

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT_WORD_RE = re.compile(r"^\d*>+[|!]*(.*)$")


@dataclass(frozen=True)
class BlockVerdict:
    """Why a command/write was refused. ``reason`` is the model-facing
    message and MUST name the sanctioned path (ADR-013 G1 refusal UX)."""

    code: str
    reason: str


def _verdict(code: str, action: str) -> BlockVerdict:
    return BlockVerdict(
        code=code,
        reason=(
            f"BLOCKED (prod-guard, ADR-013 G1): {action}. "
            "Prod mutations route exclusively through GH Actions "
            "'production-deployment.yml' or 'scripts/deploy_prod.sh' "
            "on-host (or an enumerated NFM-1664 SRE recovery action in its "
            "own channel). If none fits, file a Paperclip issue first and "
            "run the change through a sanctioned path. Read-only prod "
            "commands (docker ps/inspect/logs/stats, cat/grep, "
            "docker compose config) remain allowed."
        ),
    )


# ---------------------------------------------------------------------------
# Command splitting
# ---------------------------------------------------------------------------

def _split_top_level(command: str) -> list[str]:
    """Split a command on unquoted ``; && || | &`` and newlines.

    Quote-aware and backslash-aware walk over the raw string. Substitution
    payloads (``$( )`` and backticks) stay inside their segment — they are
    extracted and scanned separately by :func:`_subshell_payloads`.
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(command[i + 1])
                i += 1
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(command[i + 1])
            i += 2
            continue
        if ch == "|" and buf and buf[-1] == ">":
            # `>|` clobber-override redirect, not a pipe
            buf.append(ch)
            i += 1
            continue
        if ch in (";", "|", "&", "\n"):
            j = i + 1
            while j < n and command[j] in (";", "|", "&"):
                j += 1
            segments.append("".join(buf))
            buf = []
            i = j
            continue
        if ch == "#" and (not buf or buf[-1].isspace()):
            while i < n and command[i] != "\n":
                i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s for s in (seg.strip() for seg in segments) if s]


def _subshell_payloads(command: str) -> list[str]:
    """Extract ``$( … )`` and ```…` `` payloads (one nesting level)."""
    payloads: list[str] = []
    i, n = 0, len(command)
    while i < n:
        if command[i] == "$" and i + 1 < n and command[i + 1] == "(":
            depth = 0
            j = i + 1
            start = j + 1
            while j < n:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                    if depth == 0:
                        payloads.append(command[start:j])
                        i = j + 1
                        break
                j += 1
            else:
                break
            continue
        if command[i] == "`":
            j = command.find("`", i + 1)
            if j == -1:
                break
            payloads.append(command[i + 1 : j])
            i = j + 1
            continue
        i += 1
    return [p for p in payloads if p.strip()]


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _strip_leading_wrappers(words: list[str]) -> list[str]:
    """Drop sudo/env/assignment/nohup-style prefixes to reach word 0.

    Only the HEAD analysis uses the stripped list — prod markers are detected
    on the full segment text so ``PROD_IMAGE_TAG=x docker compose …`` keeps
    its marker after the assignment is stripped here.
    """
    out = list(words)
    while out:
        head = out[0]
        if head in _WRAPPER_COMMANDS:
            out = out[1:]
            if head == "sudo":
                # sudo flag cluster (-E; value-taking -u root)
                while out:
                    if out[0] in _SUDO_VALUE_FLAGS:
                        out = out[2:]
                    elif out[0].startswith("-"):
                        out = out[1:]
                    else:
                        break
            elif head == "env":
                # env -i / env -u VAR / env --unset=VAR
                while out:
                    if out[0] in _ENV_VALUE_FLAGS:
                        out = out[2:]
                    elif out[0].startswith("-"):
                        out = out[1:]
                    else:
                        break
            elif head == "timeout":
                # timeout [-k N] [-s SIG] [--foreground] DURATION cmd
                while out:
                    if out[0] in _TIMEOUT_VALUE_FLAGS:
                        out = out[2:]
                    elif out[0].startswith("-"):
                        out = out[1:]
                    else:
                        break
                if out:
                    out = out[1:]  # the DURATION argument
            elif head == "nice":
                # nice [-n N] cmd
                while out:
                    if out[0] in _NICE_VALUE_FLAGS:
                        out = out[2:]
                    elif out[0].startswith("-"):
                        out = out[1:]
                    else:
                        break
            elif head == "stdbuf":
                # stdbuf -oL -eL cmd — separate-value forms (-i/-o/-e SIZE)
                # consume two words; attached forms (-oL, -i0) consume one.
                while out:
                    if out[0] in ("-i", "-o", "-e"):
                        out = out[2:]
                    elif out[0].startswith("-"):
                        out = out[1:]
                    else:
                        break
            continue
        if _ASSIGNMENT_RE.match(head):
            out = out[1:]
            continue
        break
    return out


def _find_subcommand(words: Sequence[str], start: int,
                     value_flags: frozenset) -> int | None:
    """Index of the first non-flag word at/after ``start``, skipping flags
    and the word consumed by value-taking flags."""
    i = start
    n = len(words)
    while i < n:
        w = words[i]
        if w in value_flags:
            i += 2  # flag + its value
            continue
        if w.startswith("-") and w != "-":
            i += 1  # valueless flag or --flag=value form
            continue
        return i
    return None


# ---------------------------------------------------------------------------
# Marker detection (operate on the FULL segment text — markers may sit in
# wrapper assignments, flag values, or any argument position)
# ---------------------------------------------------------------------------

def _segment_has_prod_marker(segment_lower: str,
                             words_lower: Sequence[str] = ()) -> bool:
    if PROD_PROJECT_NAME in segment_lower:
        return True
    if any(marker in segment_lower for marker in PROD_FILE_MARKERS):
        return True
    # NFM-4284 N1: an unquoted backslash-escaped marker
    # (docker-compose\.prod\.yml) never appears as a raw substring, but
    # shlex has already unescaped it in the segment words — scan those too.
    # Quote-literal backslashes survive shlex and correctly do NOT match:
    # that argv names a different (nonexistent) file, not the prod stack.
    for word in words_lower:
        if (PROD_PROJECT_NAME in word
                or any(marker in word for marker in PROD_FILE_MARKERS)):
            return True
    return False


def _hits_prod_file_marker(word: str) -> bool:
    """Single-word/operand marker test, honouring the README's never-blocked
    carve-out: ``.env.prod.example`` stays writable in every layer."""
    for marker in PROD_FILE_MARKERS:
        if marker in word:
            if marker == ".env.prod" and word.endswith(".env.prod.example"):
                continue
            return True
    return False


# ---------------------------------------------------------------------------
# Segment evaluation
# ---------------------------------------------------------------------------

def _redirect_to_prod(words_lower: Sequence[str]) -> bool:
    """``>``/``>>``/``2>`` redirection whose target is a prod file."""
    for i, w in enumerate(words_lower):
        m = _REDIRECT_WORD_RE.match(w)
        if not m:
            continue
        target = m.group(1)
        if not target and i + 1 < len(words_lower):
            target = words_lower[i + 1]
        if target and _hits_prod_file_marker(target):
            return True
    return False


def _evaluate_segment(segment: str) -> BlockVerdict | None:
    words = _words(segment)
    if not words:
        return None
    segment_lower = segment.lower()

    # 1) Redirection to a prod file — any head command, checked FIRST so
    #    `docker compose config > docker-compose.prod.yml` cannot slip past
    #    the read-only compose branch below.
    if _redirect_to_prod([w.lower() for w in words]):
        return _verdict(
            "prod_file_write",
            "shell redirection writes a production compose/env file",
        )

    stripped = _strip_leading_wrappers(words)
    if not stripped:
        return None
    words_lower = [w.lower() for w in stripped]
    head = words_lower[0].rsplit("/", 1)[-1]  # tolerate absolute binary paths

    # 2) docker compose / docker-compose --------------------------------
    compose_head_at: int | None = None
    if head == "docker-compose":
        compose_head_at = 0
    elif head == "docker":
        first = _find_subcommand(words_lower, 1, _DOCKER_VALUE_FLAGS)
        if first is not None and words_lower[first] == "compose":
            compose_head_at = first
    if compose_head_at is not None:
        sub_at = _find_subcommand(
            words_lower, compose_head_at + 1, _COMPOSE_VALUE_FLAGS)
        if (sub_at is not None
                and words_lower[sub_at] in COMPOSE_MUTATION_VERBS
                and _segment_has_prod_marker(
                    segment_lower, [w.lower() for w in words])):
            return _verdict(
                "prod_compose_mutation",
                f"'docker compose {words_lower[sub_at]}' targets the "
                "production compose stack",
            )
        return None  # read-only compose verb, or no prod marker

    # 3) bare docker container verbs ------------------------------------
    if head == "docker":
        sub_at = _find_subcommand(words_lower, 1, _DOCKER_VALUE_FLAGS)
        if (sub_at is not None
                and words_lower[sub_at] == "container"):
            # `docker container <verb>` management spelling
            next_at = _find_subcommand(
                words_lower, sub_at + 1, _DOCKER_VALUE_FLAGS)
            if (next_at is not None
                    and words_lower[next_at] in DOCKER_CONTAINER_MUTATION_VERBS):
                sub_at = next_at
        if (sub_at is not None
                and words_lower[sub_at] in DOCKER_CONTAINER_MUTATION_VERBS):
            for w in words_lower[sub_at + 1:]:
                if (w == PROD_PROJECT_NAME
                        or w.startswith(PROD_CONTAINER_PREFIX + "-")
                        or w.startswith(PROD_CONTAINER_PREFIX + ":")):
                    return _verdict(
                        "prod_container_mutation",
                        f"'docker {words_lower[sub_at]}' targets a production "
                        "container",
                    )
        return None

    # 4) command-shaped writes to prod files ----------------------------
    return _evaluate_write_shape(words_lower)


def _evaluate_write_shape(words_lower: list[str]) -> BlockVerdict | None:
    if not words_lower:
        return None
    sub = words_lower[0].rsplit("/", 1)[-1]

    # tee [-a] <file…>
    if sub == "tee":
        for w in words_lower[1:]:
            if not w.startswith("-") and _hits_prod_file_marker(w):
                return _verdict(
                    "prod_file_write",
                    "'tee' writes a production compose/env file",
                )

    # sed -i[.ext] / --in-place <file>
    if sub == "sed" and any(
            w == "-i" or w.startswith("-i.") or w.startswith("--in-place")
            for w in words_lower[1:]):
        for w in words_lower[1:]:
            if not w.startswith("-") and _hits_prod_file_marker(w):
                return _verdict(
                    "prod_file_write",
                    "'sed -i' edits a production compose/env file in place",
                )

    # cp/mv/install <src…> <dst> — only the LAST operand (destination)
    if sub in ("cp", "mv", "install") and len(words_lower) >= 3:
        operands = [w for w in words_lower[1:] if not w.startswith("-")]
        if operands and _hits_prod_file_marker(operands[-1]):
            return _verdict(
                "prod_file_write",
                f"'{sub}' destination is a production compose/env file",
            )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_command(command: str) -> BlockVerdict | None:
    """Return a :class:`BlockVerdict` when ``command`` is a G1 prod
    mutation, else ``None``."""
    if not isinstance(command, str) or not command.strip():
        return None
    for payload in _subshell_payloads(command):
        verdict = evaluate_command(payload)
        if verdict is not None:
            return verdict
    for segment in _split_top_level(command):
        verdict = _evaluate_segment(segment)
        if verdict is not None:
            return verdict
    return None


def _normalize_path(path: str) -> str:
    out = path.strip()
    if out.startswith("~"):
        import os

        out = os.path.expanduser(out)
    out = out.replace("\\", "/")
    while out.startswith("./"):
        out = out[2:]
    return out


def evaluate_write_target(path: str) -> BlockVerdict | None:
    """Return a :class:`BlockVerdict` when ``path`` is a prod compose/env
    file the agent is about to write via write_file/patch — the vector
    observed in NFM-4264 (patch attempt #2 succeeded at 23:42:55)."""
    if not isinstance(path, str) or not path.strip():
        return None
    norm = _normalize_path(path).lower()
    if _hits_prod_file_marker(norm):
        return _verdict(
            "prod_file_write_tool",
            f"refusing agent write to production file '{path}'",
        )
    return None


def is_prod_touching(command: str) -> bool:
    """G3 scope test: does this command reference prod compose state at all
    (mutations AND read-only touches)? Bounds the success-path log."""
    if not isinstance(command, str) or not command.strip():
        return False
    lowered = command.lower()
    if (any(marker in lowered for marker in PROD_FILE_MARKERS)
            or PROD_CONTAINER_PREFIX in lowered):
        return True
    # NFM-4284 N1: escaped markers are invisible to the raw substring scan;
    # the shlex-unescaped words reveal them, so read-only successes like
    # `cat docker-compose\.prod\.yml` still bound the G3 log.
    if _segment_has_prod_marker(lowered, [w.lower() for w in _words(command)]):
        return True
    return any(is_prod_touching(p) for p in _subshell_payloads(command))
