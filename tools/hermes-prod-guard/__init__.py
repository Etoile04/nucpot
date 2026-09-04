"""prod-guard — Hermes plugin enforcing ADR-013 G1 (deny) + G3 (observe).

NFM-4269. Hooks:

* ``pre_tool_call`` — blocks Hermes-terminal prod-compose mutations and
  agent writes (``write_file``/``patch``) to the prod compose/env files,
  with a refusal message naming the sanctioned path. FAIL-CLOSED: any
  internal error blocks the call rather than allowing it (ADR-013 §2 G1
  — the prod-mutation rules must not inherit ``tirith_fail_open``).
* ``post_tool_call`` — G3 observability: every prod-touching terminal
  command is logged at INFO with its FULL literal text and status,
  including successes (the NFM-4264 attribution gap). Secret-shaped
  assignments in the command line are redacted to honour
  ``security.redact_secrets: true``.

All matching logic lives in :mod:`prod_guard` (pure stdlib, unit-tested
in the nucpot repo at ``tests/tools/test_hermes_prod_guard.py``).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from . import prod_guard

logger = logging.getLogger(__name__)

# Tools whose args carry a shell command line.
_TERMINAL_TOOLS = frozenset({"terminal"})
# Tools whose args carry a file write target.
_WRITE_TOOLS = frozenset({"write_file", "patch"})

_FAIL_CLOSED_MESSAGE = (
    "BLOCKED (prod-guard, ADR-013 G1): fail-closed — the prod-guard hook "
    "hit an internal error while evaluating this call, so it is refused by "
    "default. Prod mutations route exclusively through GH Actions "
    "'production-deployment.yml' or 'scripts/deploy_prod.sh' on-host (or "
    "an enumerated NFM-1664 SRE recovery action in its own channel). If "
    "none fits, file a Paperclip issue first."
)

# Fallback redaction when Hermes' agent.redact helper is unavailable
# (e.g. plugin loaded outside a full gateway install).
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z0-9_-]*"
    r"(?:password|passwd|secret|token|api_key|apikey|access_key|"
    r"private_key|credential)s?)\s*[=:]\s*(\S+)"
)


def _redact(text: str) -> str:
    """Honour ``security.redact_secrets`` for G3 command logging."""
    try:
        from agent.redact import redact_sensitive_text  # type: ignore

        return redact_sensitive_text(text)
    except Exception:
        return _SECRET_ASSIGN_RE.sub(
            lambda m: f"{m.group(1)}=<redacted>", text)


def _block(verdict, evidence: str = "") -> dict[str, str]:
    logger.warning(
        "BLOCKED (prod-guard): %s (command: %s)",
        verdict.code,
        _redact(evidence),
    )
    return {"action": "block", "message": verdict.reason}


def register(ctx) -> None:  # pragma: no cover - wiring, exercised via tests
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)


def _on_pre_tool_call(tool_name: str = "", args: dict[str, Any] | None = None,
                      task_id: str = "", session_id: str = "",
                      tool_call_id: str = "", turn_id: str = "",
                      api_request_id: str = "", **_) -> dict[str, str] | None:
    try:
        if tool_name in _TERMINAL_TOOLS:
            command = ""
            if isinstance(args, dict):
                command = args.get("command") or ""
            verdict = prod_guard.evaluate_command(command)
            if verdict is not None:
                return _block(verdict, str(command))
            return None
        if tool_name in _WRITE_TOOLS:
            path = ""
            if isinstance(args, dict):
                path = args.get("path") or args.get("file_path") or ""
            verdict = prod_guard.evaluate_write_target(path)
            if verdict is not None:
                return _block(verdict, str(path))
            return None
        return None
    except Exception as exc:  # deliberate fail-closed catch-all
        logger.warning("prod-guard pre hook error (fail-closed): %s", exc)
        return {"action": "block", "message": _FAIL_CLOSED_MESSAGE}


def _on_post_tool_call(tool_name: str = "", args: dict[str, Any] | None = None,
                       result: Any = None, status: str = "",
                       duration_ms: Any = None, task_id: str = "",
                       session_id: str = "", tool_call_id: str = "",
                       turn_id: str = "", api_request_id: str = "",
                       **_) -> None:
    try:
        if tool_name not in _TERMINAL_TOOLS or status == "blocked":
            return  # blocked calls are already logged by the pre hook
        command = ""
        if isinstance(args, dict):
            command = args.get("command") or ""
        if not command or not prod_guard.is_prod_touching(command):
            return
        logger.info(
            "PROD-TOUCHING terminal command (status=%s, duration_ms=%s): %s",
            status, duration_ms, _redact(str(command)),
        )
    except Exception as exc:  # observe-only, never break the tool
        logger.debug("prod-guard post hook error: %s", exc)
