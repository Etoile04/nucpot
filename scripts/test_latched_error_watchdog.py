"""Unit tests for latched_error_watchdog.py.

Covers the pure-logic surface (is_latched, classify_workspace,
build_fingerprint, build_issue_title, build_issue_body, extract_sentinel,
find_existing_issue) without touching the network. The HTTP path is
exercised by the smoke-test step in the runbook.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from latched_error_watchdog import (
    DEFAULT_WORKSPACE_ROOT,
    EXIT_DRY_RUN_LATCHED,
    EXIT_ERROR,
    EXIT_SUCCESS,
    build_dedup_comment,
    build_fingerprint,
    build_issue_body,
    build_issue_title,
    classify_workspace,
    extract_sentinel,
    find_existing_issue,
    is_latched,
    main,
    parse_agents_response,
    run_watchdog,
)


def _iso(minutes_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def _agent(
    *,
    agent_id: str = "11111111-1111-1111-1111-111111111111",
    name: str = "TestAgent",
    status: str = "error",
    adapter: str = "claude_local",
    last_hb_min_ago: int | None = 30,
    error_reason: str | None = "fatal: not a git repository",
) -> dict[str, object]:
    return {
        "id": agent_id,
        "name": name,
        "urlKey": name.lower(),
        "status": status,
        "adapterType": adapter,
        "lastHeartbeatAt": _iso(last_hb_min_ago) if last_hb_min_ago is not None else None,
        "errorReason": error_reason,
    }


class TestIsLatched(unittest.TestCase):
    """Filter: claude_local + status=error + stale heartbeat."""

    def test_latched_claude_local_error_with_stale_heartbeat(self) -> None:
        self.assertTrue(is_latched(_agent(), threshold_minutes=10, now=datetime.now(UTC)))

    def test_not_latched_heartbeat_within_threshold(self) -> None:
        self.assertFalse(
            is_latched(_agent(last_hb_min_ago=5), threshold_minutes=10, now=datetime.now(UTC))
        )

    def test_not_latched_running_status(self) -> None:
        self.assertFalse(
            is_latched(
                _agent(status="running", last_hb_min_ago=120),
                threshold_minutes=10,
                now=datetime.now(UTC),
            )
        )

    def test_not_latched_idle_status(self) -> None:
        self.assertFalse(
            is_latched(
                _agent(status="idle", last_hb_min_ago=120),
                threshold_minutes=10,
                now=datetime.now(UTC),
            )
        )

    def test_not_latched_openclaw_adapter_out_of_scope(self) -> None:
        self.assertFalse(
            is_latched(
                _agent(adapter="openclaw_gateway", last_hb_min_ago=120),
                threshold_minutes=10,
                now=datetime.now(UTC),
            )
        )

    def test_never_heartbeated_is_latched(self) -> None:
        self.assertTrue(
            is_latched(_agent(last_hb_min_ago=None), threshold_minutes=10, now=datetime.now(UTC))
        )

    def test_threshold_zero_marks_recent_heartbeat_latched(self) -> None:
        # threshold=0 means anything older than the instant of "now" is stale.
        self.assertTrue(
            is_latched(_agent(last_hb_min_ago=0), threshold_minutes=0, now=datetime.now(UTC))
        )


class TestClassifyWorkspace(unittest.TestCase):
    """Workspace probe: never-provisioned vs transient vs terminal."""

    def test_workspace_missing_is_never_provisioned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ghost = os.path.join(tmp, "ghost-ws")
            kind, probe = classify_workspace(ghost)
            self.assertEqual(kind, "never_provisioned")
            self.assertIn("workspace missing", probe)

    def test_workspace_present_no_git_anchor_is_transient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kind, probe = classify_workspace(tmp)
            self.assertEqual(kind, "transient")
            self.assertIn("missing .git anchor", probe)

    def test_workspace_with_dot_git_dir_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, ".git"))
            kind, probe = classify_workspace(tmp)
            self.assertEqual(kind, "terminal")
            self.assertIn(".git anchor present", probe)

    def test_workspace_with_dot_git_file_is_terminal(self) -> None:
        # Some git worktrees use a .git file (not a directory) pointing at the
        # common gitdir. Accept both.
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, ".git"), "w") as f:
                f.write("gitdir: /tmp/whatever\n")
            kind, _ = classify_workspace(tmp)
            self.assertEqual(kind, "terminal")


class TestBuildFingerprint(unittest.TestCase):
    def test_fingerprint_is_stable_for_same_inputs(self) -> None:
        a = build_fingerprint("agent-1", "fatal: not a git repository")
        b = build_fingerprint("agent-1", "fatal: not a git repository")
        self.assertEqual(a, b)

    def test_fingerprint_changes_with_agent_id(self) -> None:
        a = build_fingerprint("agent-1", "x")
        b = build_fingerprint("agent-2", "x")
        self.assertNotEqual(a, b)

    def test_fingerprint_changes_with_error_reason(self) -> None:
        a = build_fingerprint("agent-1", "reason-a")
        b = build_fingerprint("agent-1", "reason-b")
        self.assertNotEqual(a, b)

    def test_fingerprint_handles_none_error_reason(self) -> None:
        a = build_fingerprint("agent-1", None)
        b = build_fingerprint("agent-1", None)
        self.assertEqual(a, b)
        c = build_fingerprint("agent-1", "")
        # None and empty string should NOT collide — they are different signals.
        self.assertNotEqual(a, c)


class TestBuildIssueTitle(unittest.TestCase):
    def test_title_format(self) -> None:
        title = build_issue_title(_agent(name="Strategy Director"))
        self.assertTrue(title.startswith("[SRE-LATCHED]"))
        self.assertIn("Strategy Director", title)
        self.assertIn("clear-error", title)


class TestBuildIssueBody(unittest.TestCase):
    def test_body_contains_required_fields(self) -> None:
        agent = _agent()
        body = build_issue_body(
            agent,
            fingerprint="abc123",
            classification="transient",
            workspace_probe="missing .git",
            threshold_minutes=10,
            now=datetime.now(UTC),
        )
        self.assertIn(agent["id"], body)
        self.assertIn("claude_local", body)
        self.assertIn("TRANSIENT", body)
        self.assertIn("fatal: not a git repository", body)
        self.assertIn("POST", body)
        self.assertIn("/api/agents/", body)
        self.assertIn("/clear-error", body)
        self.assertIn("git init -q .", body)
        self.assertIn("agentId=", body)
        self.assertIn("fingerprint=abc123", body)

    def test_body_classification_terminal(self) -> None:
        body = build_issue_body(
            _agent(),
            fingerprint="x",
            classification="terminal",
            workspace_probe="ok",
            threshold_minutes=10,
            now=datetime.now(UTC),
        )
        self.assertIn("TERMINAL", body)
        self.assertNotIn("git init -q .", body)


class TestBuildDedupComment(unittest.TestCase):
    def test_comment_has_sentinel_and_check_label(self) -> None:
        comment = build_dedup_comment(
            _agent(),
            fingerprint="fp-1",
            threshold_minutes=10,
            now=datetime.now(UTC),
        )
        self.assertIn("[SRE-LATCHED-CHECK]", comment)
        self.assertIn("agentId=", comment)
        self.assertIn("fingerprint=fp-1", comment)


class TestExtractSentinel(unittest.TestCase):
    def test_extracts_from_description(self) -> None:
        body = (
            "## Latched error detected\n"
            "...\n"
            "<!-- latched-watchdog: agentId=abc fingerprint=fp1 version=v1 -->\n"
        )
        sentinel = extract_sentinel(body)
        self.assertIsNotNone(sentinel)
        self.assertEqual(sentinel["agentId"], "abc")
        self.assertEqual(sentinel["fingerprint"], "fp1")
        self.assertEqual(sentinel["version"], "v1")

    def test_returns_none_when_no_sentinel(self) -> None:
        self.assertIsNone(extract_sentinel("just a normal comment"))
        self.assertIsNone(extract_sentinel(""))
        self.assertIsNone(extract_sentinel(None))


class TestFindExistingIssue(unittest.TestCase):
    def _make_issue(
        self,
        *,
        agent_id: str,
        fingerprint: str,
        status: str = "todo",
        updated_minutes_ago: int = 5,
    ) -> dict[str, object]:
        return {
            "id": f"issue-{agent_id[:8]}",
            "identifier": f"NFM-{hash(agent_id) % 10000}",
            "title": f"[SRE-LATCHED] agent {agent_id[:8]} stuck in error",
            "description": (
                "## Latched error detected\n"
                f"<!-- latched-watchdog: agentId={agent_id} "
                f"fingerprint={fingerprint} version=v1 -->\n"
            ),
            "status": status,
            "updatedAt": _iso(updated_minutes_ago),
            "createdAt": _iso(updated_minutes_ago + 1),
        }

    def test_finds_matching_open_issue(self) -> None:
        existing = self._make_issue(agent_id="agent-1", fingerprint="fp-1")
        match = find_existing_issue(
            [existing],
            agent_id="agent-1",
            fingerprint="fp-1",
            dedup_hours=6,
            now=datetime.now(UTC),
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "issue-agent-1")

    def test_skips_different_fingerprint(self) -> None:
        existing = self._make_issue(agent_id="agent-1", fingerprint="fp-old")
        match = find_existing_issue(
            [existing],
            agent_id="agent-1",
            fingerprint="fp-new",
            dedup_hours=6,
            now=datetime.now(UTC),
        )
        self.assertIsNone(match)

    def test_skips_closed_issue(self) -> None:
        existing = self._make_issue(agent_id="agent-1", fingerprint="fp-1", status="done")
        match = find_existing_issue(
            [existing],
            agent_id="agent-1",
            fingerprint="fp-1",
            dedup_hours=6,
            now=datetime.now(UTC),
        )
        self.assertIsNone(match)

    def test_skips_old_issue_outside_dedup_window(self) -> None:
        existing = self._make_issue(
            agent_id="agent-1",
            fingerprint="fp-1",
            updated_minutes_ago=60 * 24 * 7,  # 7 days old
        )
        match = find_existing_issue(
            [existing],
            agent_id="agent-1",
            fingerprint="fp-1",
            dedup_hours=6,
            now=datetime.now(UTC),
        )
        self.assertIsNone(match)


class TestParseAgentsResponse(unittest.TestCase):
    def test_returns_empty_for_none(self) -> None:
        self.assertEqual(parse_agents_response(None), [])

    def test_returns_empty_for_empty_list(self) -> None:
        self.assertEqual(parse_agents_response([]), [])

    def test_passes_through_items(self) -> None:
        items = [{"id": "1"}, {"id": "2"}]
        self.assertEqual(parse_agents_response(items), items)


class TestWorkspaceRootDefault(unittest.TestCase):
    """Regression: default workspace root must point at the Paperclip
    claude_local agent home (not a generic 'instances/default/workspaces').
    NFM-3995 found that without `.paperclip` in the path, the watchdog SKIPs
    every agent as 'never_provisioned' (false negative) — defeating AC-1.
    """

    def test_default_workspace_root_is_paperclip_relative(self) -> None:
        # The actual root used by claude_local adapters on this host.
        # See AGENT_HOME / PAPERCLIP_WORKSPACE_CWD env vars injected at runtime.
        self.assertTrue(
            DEFAULT_WORKSPACE_ROOT.endswith(".paperclip/instances/default/workspaces"),
            f"DEFAULT_WORKSPACE_ROOT={DEFAULT_WORKSPACE_ROOT!r} must end with "
            "'.paperclip/instances/default/workspaces' to find claude_local agent homes",
        )

    def test_default_workspace_root_is_absolute(self) -> None:
        # expanduser must have resolved '~' so the watchdog never reads a
        # literal '~' at runtime (that would skip every agent).
        self.assertFalse(
            DEFAULT_WORKSPACE_ROOT.startswith("~"),
            f"DEFAULT_WORKSPACE_ROOT={DEFAULT_WORKSPACE_ROOT!r} must be absolute "
            "(~ should be expanded at import time)",
        )


class TestRunWatchdog(unittest.TestCase):
    """End-to-end of run_watchdog with a fake HTTP client."""

    def _mock_api(self, agents: list[dict[str, Any]], issues: list[dict[str, Any]]) -> MagicMock:
        api = MagicMock()
        api.get.side_effect = lambda path, query=None: (
            agents if "/agents" in path and "/companies" in path else issues
        )
        api.post.return_value = {"id": "new-id", "identifier": "NFM-9999"}
        return api

    def test_clean_run_exits_zero(self) -> None:
        agents = [
            _agent(agent_id="a1", status="running"),
            _agent(agent_id="a2", status="idle", last_hb_min_ago=120),
        ]
        api = self._mock_api(agents, [])
        rc, latched = run_watchdog(
            api,
            "company-1",
            threshold_minutes=10,
            dedup_hours=6,
            dry_run=False,
            workspace_root=tempfile.gettempdir(),
        )
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(latched, [])

    def test_latched_run_dry_run_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # The watchdog looks up workspace_root/{agent_id}; create it so
            # classify_workspace returns "transient" instead of "never_provisioned".
            agent_id = "a1"
            os.mkdir(os.path.join(tmp, agent_id))
            agents = [_agent(agent_id=agent_id, last_hb_min_ago=120)]
            api = self._mock_api(agents, [])
            rc, latched = run_watchdog(
                api,
                "company-1",
                threshold_minutes=10,
                dedup_hours=6,
                dry_run=True,
                workspace_root=tmp,
            )
            self.assertEqual(rc, EXIT_DRY_RUN_LATCHED)
            self.assertEqual(len(latched), 1)
            self.assertEqual(latched[0].classification, "transient")
            # Dry run: no issue should have been created
            api.post.assert_not_called()

    def test_latched_run_dedup_reuses_existing_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent_id = "11111111-1111-1111-1111-111111111111"
            # Create the agent's workspace so classification = "transient".
            os.mkdir(os.path.join(tmp, agent_id))
            agents = [_agent(agent_id=agent_id, last_hb_min_ago=120)]
            existing_issue = {
                "id": "issue-1",
                "identifier": "NFM-1000",
                "title": "[SRE-LATCHED] TestAgent stuck in error — Board clear-error needed",
                "description": (
                    "## Latched error detected\n"
                    f"<!-- latched-watchdog: agentId={agent_id} "
                    f"fingerprint={build_fingerprint(agent_id, 'fatal: not a git repository')} "
                    "version=v1 -->\n"
                ),
                "status": "todo",
                "updatedAt": _iso(1),
                "createdAt": _iso(2),
            }
            api = self._mock_api(agents, [existing_issue])
            rc, latched = run_watchdog(
                api,
                "company-1",
                threshold_minutes=10,
                dedup_hours=6,
                dry_run=False,
                workspace_root=tmp,
            )
            self.assertEqual(rc, EXIT_SUCCESS)
            self.assertEqual(len(latched), 1)
            # Dedup: dedup comment was posted (NOT a new issue)
            posted_paths = [call.args[0] for call in api.post.call_args_list]
            self.assertTrue(any("/comments" in p for p in posted_paths))
            self.assertFalse(any(p.endswith("/issues") for p in posted_paths))


class TestMain(unittest.TestCase):
    def test_missing_env_vars_returns_error(self) -> None:
        saved = {
            "PAPERCLIP_API_URL": os.environ.pop("PAPERCLIP_API_URL", None),
            "PAPERCLIP_API_KEY": os.environ.pop("PAPERCLIP_API_KEY", None),
            "PAPERCLIP_COMPANY_ID": os.environ.pop("PAPERCLIP_COMPANY_ID", None),
        }
        try:
            rc = main(argparse_namespace(loop=False))
            self.assertEqual(rc, EXIT_ERROR)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


def argparse_namespace(*, loop: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        threshold_minutes=10,
        dedup_hours=6,
        workspace_root=tempfile.gettempdir(),
        dry_run=False,
        once=True,
        loop_seconds=0 if not loop else 60,
        verbose=False,
    )


if __name__ == "__main__":
    unittest.main()
