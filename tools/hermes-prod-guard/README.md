# hermes-prod-guard — ADR-013 G1+G3 (NFM-4269)

Harness-layer guardrails against the NFM-4264 incident class: a long-lived
Hermes desktop session ran `docker compose --env-file docker/.env.prod up -d
--build api web` directly against prod, bypassing every path-based control,
with zero audit trail on success.

**G1 (deny)** — refuse prod-compose mutations from the Hermes terminal tool.
**G3 (observe)** — log the full literal command line for prod-touching
terminal commands, *including successes* (the ~6h attribution gap).

## Layers

| Layer | What | Enforced where | Fails how |
| --- | --- | --- | --- |
| Plugin `pre_tool_call` | precise parser (wrappers, subshells, all mutation verbs, project-name forms, `write_file`/`patch` targets) + refusal message naming the sanctioned path | `hermes_cli.plugins` dispatcher seam — tool never executes | closed (internal error ⇒ block) |
| Plugin `post_tool_call` | G3 logging: `PROD-TOUCHING terminal command (status=…, duration_ms=…): <full text>` at INFO, secrets redacted | same seam, on completion | open (observe-only, debug-log) |
| `approvals.deny` belt | coarse fnmatch globs over the raw lowercased command | `tools/approval.py::_match_user_deny_rule`, **before** yolo/approval bypass, in-process (tirith `fail_open` does not apply), live-reloads on config mtime | closed (it's a plain list) |

Why both: the plugin is precise but lives in user-editable
`~/.hermes/plugins/`; the belt lives in `~/.hermes/config.yaml`, which the
Hermes write tool itself refuses to let agents edit
(`_check_sensitive_path`), and which needs no gateway restart. The belt also
denies `rm`/`mv` on `plugins/prod-guard*` so an agent can't quietly delete
layer 1. Belt is deliberately coarse (it can over-block compound commands
that merely *mention* `nucpot-prod` next to a compose `up`); the plugin is
the precise layer — belt misses are acceptable, belt false-positives on
read-only ops are not (tested).

## Blocked (G1)

- `docker compose`/`docker-compose` + `up down build rebuild restart stop
  start rm kill run exec` when the invocation references
  `docker-compose.prod.yml`, `.env.prod` (any `--env-file`/`-f`/argument
  spelling), or the project name `nucpot-prod` (`-p`, `--project-name`,
  `COMPOSE_PROJECT_NAME=…` prefix) — through
  `sudo`/`env`/`timeout`/`nice`/`stdbuf`/`nohup`/`cd … &&`, pipes,
  subshells `$(…)`/backticks
- bare `docker stop|rm|restart|kill|exec nucpot-prod…` and the
  `docker container <verb>` spelling of the same mutations
- terminal writes to the prod files: `>`/`>>` redirects (any head command —
  including `docker compose config > docker-compose.prod.yml`), `tee`,
  `sed -i`, `cp`/`mv`/`install` with a prod destination
- backslash-escape obfuscation of any marker (NFM-4284 N1):
  `docker compose -f docker-compose\.prod\.yml up -d` is blocked in BOTH
  layers — the plugin detects markers on the shlex-unescaped words (so an
  unquoted escape cannot hide one), and the belt carries `[ .\-]*`/`[.\]*`
  character-class globs that absorb the optional backslash before each
  dot/hyphen, before letters of the marker/verb (`pro\d`, `u\p`), in the
  two-char head separator (`docker\-compose`), and across the write
  vectors (redirect/`tee`/`sed -i`/`cp`/`mv`/`install` destinations).
  Quote-literal escapes (`'docker-compose\.prod\.yml'`) name a
  different, nonexistent file and correctly stay allowed
- `\`+LF line continuations (NFM-4284 N1): bash drops the pair —
  unquoted AND inside double quotes — so the plugin drops it before
  tokenization the same way (single quotes keep the pair literal) and
  a continuation before the verb, before the head, or inside a marker
  cannot mask the mutation words; the belt carries
  continuation-anchored entries for the compose verbs, the bare
  container verbs, and the double-quoted split-verb spelling
- agent `write_file`/`patch` with `path` on either prod file

## Never blocked (verified)

`docker ps/inspect/logs/stats`, `cat`/`grep` of prod files,
`docker compose config` (render-only), `docker build` of prod images, the
staging stack (`docker-compose.staging.yml`/`.env.staging`/`nucpot-staging`),
`docker image prune` (NFM-4257 cron), non-prod containers, reads *from* prod
files redirected *to* tmp, `docker/.env.prod.example`, and bare
`docker start nucpot-prod-api-1` (NFM-1664 AC3 recovery carve-out —
`start` is not a bare-docker container verb; pinned by NFM-4284 N2 so a
verb-scope change cannot silently break recovery).

## Carve-outs (keyed on the aecb57d3 deploy-identity marker, per NFM-4274)

"Sanctioned" = execution under a **dedicated local deploy identity**,
acquired only via command-enumerated sudo at the sanctioned chokepoints
(deploy_prod.sh, GH runner `production-deployment.yml` step, enumerated
NFM-1664 recovery entries) — canonical contract: NFM-4268 comment
`aecb57d3`, binding on this design via NFM-4274. Under that definition the
in-session sanctioned set is **empty**: acquiring the identity requires a
root-level, sudo-log-audited act outside a bare terminal command from an
agent session, so `sudo -u <deploy-identity>` typed into a Hermes session
is NOT a chokepoint and is blocked like any other wrapper, and no env-var
marker is honored (explicitly rejected by ADR-013 G1 — an agent can set it
inline). G2 (NFM-4270) implements the same definition host-side; the
layers compose at NFM-4273 integration.

- **`deploy_prod.sh`**: invoked by GH Actions `production-deployment.yml`
  (scp to host + execute) and directly on-host by the operator — its child
  compose processes never route through the Hermes terminal tool, so there
  is no token to forge; a bare `bash scripts/deploy_prod.sh` from a Hermes
  session is allowed by design (it IS the sanctioned path).
- **GH self-hosted runner**: runs outside the Hermes gateway.
- **NFM-1664 SRE recovery**: runs in the SRE agent's own harness (Paperclip
  agents are not Hermes sessions), also outside this seam.

There is deliberately **no env-var override** — a carve-out an agent can set
in the same command line is not a carve-out (ADR-013 §2 G1).

## Install

```bash
bash tools/hermes-prod-guard/install.sh
```

Copies the plugin to `~/.hermes/plugins/prod-guard/`, backs up and merges
`~/.hermes/config.yaml` (`plugins.enabled` += `prod-guard`, `approvals.deny`
+= belt). The belt is live immediately (config mtime cache); the plugin's
hooks load at the next gateway start — restart the gateway when no live
desktop sessions depend on it.

Run `install.sh` from an operator shell: the Hermes write-tool path gate
refuses agent writes to `config.yaml`, by design.

## Test

```bash
cd apps/api && uv run pytest ../../tests/tools/test_hermes_prod_guard.py -q
```

247 cases: the exact NFM-4264 command, 92 block variants (including the
aecb57d3 marker-contract cases — `sudo -u <deploy-identity>` and env-marker
prefixes stay blocked — and the NFM-4284 N1 backslash-escape matrix:
escaped file markers, escaped project name, partially-escaped markers,
escaped head/verb/redirect/tee/cp spellings, and the `\`+LF continuation
family — unquoted, bare-container-verb, and double-quoted split-verb
spellings), 48 allow
cases (every read-only/sanctioned surface above, plus the NFM-1664 bare
`docker start` recovery carve-out pinned by NFM-4284 N2), write-target
gating, G3 log content + secret redaction + blocked-skip, fail-closed
behavior, and the belt matrix (blocks the incident class and its escaped
spellings — markers, head separator, verbs, and write-vector destinations —
no false positives on read-only/sanctioned).

## Files

- `prod_guard.py` — pure-stdlib matcher (no Hermes imports; testable anywhere)
- `__init__.py` — plugin wrapper (`register(ctx)` + both hooks)
- `plugin.yaml` — Hermes plugin manifest
- `approvals_deny.yaml` — belt entries merged into `~/.hermes/config.yaml`
- `install.sh` — idempotent installer with config backup

Reference: ADR-013 §2 G1+G3 (NFM-4266), tracked as issue NFM-4269. The
ADR file is not committed to this repo; this README and the
`prod_guard.py` module docstring are the in-repo authority for the
G1/G3 requirements.
