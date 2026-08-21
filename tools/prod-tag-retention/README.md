# tools/prod-tag-retention — NFM-3448

A small bash tool that prunes old `candidate-<sha>` tags from the
`nucpot-prod-{api,web,lightrag}` repositories in the production Docker
daemon. Companion to the existing 10-tag SHA retention prune at the end of
`production-deployment.yml::deploy-prod`.

## Why this tool exists

Each production rebuild (`pre-deploy-assert` job, ADR-NFM-2139 §5 D2) writes
two tags on the api image:

| Tag                                | Owner                                          | Lifecycle                                                                    |
|------------------------------------|------------------------------------------------|------------------------------------------------------------------------------|
| `nucpot-prod-api: candidate-<sha>` | `pre-deploy-assert` (this tool's input)        | Pinned at build time; never actively deleted until this tool runs            |
| `nucpot-prod-api: latest`          | `pre-deploy-assert` (then `docker compose`)    | Repointed to the new candidate; `docker compose up -d` reads from `:latest`  |

`deploy-prod` then writes a third tag for the actual deploy
(`PROD_IMAGE_TAG` = `github.sha`, ADR-NFM-2139 §5 D1). After deploy, the
candidate tag is redundant with the SHA tag: both reference the same image
content built twice in the same workflow.

Without intervention the daemon accumulates every candidate tag ever built
(~6 builds / 16 h at SRE's observed cadence). At ~2.24 GB per layer, the
candidate-* pile is the dominant driver of `/System/Volumes/Data` disk
pressure (`NFM-3447` [SRE-WARNING]).

## What the script does

`prune.sh --repo <nucpot-prod-api|nucpot-prod-web|nucpot-prod-lightrag> --keep <N>`

1. Lists every tag of the given repository whose name starts with
   `candidate-` (`:latest` and SHA tags are NOT touched).
2. Sorts them newest-first by Docker `CreatedAt`.
3. Removes the entries past the newest `N`.

Crucially:

- **Idempotent.** Calling it with `--keep=N` and fewer than `N+1` candidate
  tags is a no-op (exit 0, no `docker rmi` issued).
- **Repo-scoped.** Tags from other repositories (other `nucpot-prod-*`
  repos, base images, third-party caches) are not enumerated.
- **Candidate-only.** SHA tags and `:latest` are filtered out before
  counting, so the existing D1 SHA retention prune (10 tags) is unaffected.

## Wiring

The script is invoked from the tail of
`.github/workflows/production-deployment.yml::deploy-prod`, inside the same
SSH heredoc that already runs the SHA retention prune. One invocation per
repository, all with `--keep 3`:

```bash
for REPO in nucpot-prod-api nucpot-prod-lightrag nucpot-prod-web; do
  bash tools/prod-tag-retention/prune.sh --repo "$REPO" --keep 3
done
```

The `--keep 3` value matches the issue body's "max 3 candidate tags" spec
and leaves enough rollback surface for the D1 SHA-tag recipe (last 3
deploys can be re-tag-pinned into `PROD_IMAGE_TAG` if `docker tag` itself
becomes inaccessible).

## Testing

`pytest tools/prod-tag-retention/test_prune.py -v` runs 12 unit tests with
a fake-`docker` shim on PATH — no daemon required, safe on PR runners.
The existing `pre-deploy-assert-smoke` CI job invokes the same test file
alongside the assert tests so a regression in the prune logic breaks the
build before it breaks prod.

## Exit codes

| Code | Meaning                                                       |
|------|---------------------------------------------------------------|
| 0    | Success (no-op when no candidates past `keep`)                |
| 1    | Required arg missing or `--keep` is not a positive integer    |
| 2    | Unknown CLI flag                                              |
| 3    | Docker CLI missing or daemon unreachable                      |

The deploy pipeline treats exit codes ≥1 as a hard failure of the prune
step (the parent `deploy-prod` is already wrapped in `set -euo pipefail`
at the SSH-heredoc level, per NFM-3328). A "pruned N candidate tags"
message is printed on success so the workflow log shows what happened.

## Failure modes considered

| Mode                                          | Behaviour                                                            |
|-----------------------------------------------|----------------------------------------------------------------------|
| Daemon offline                                | Exit 3; deploy-prod fails loudly (NFM-3328 regression guard)         |
| Image ID shared with `:latest` / SHA tags     | Each removal targets `<repo>:<tag>`, never the bare ID. The script is structurally incapable of destroying a sibling tag (`docker image rm -f <id>` is never called). |
| Fewer than `keep` candidates                  | No-op, exit 0                                                        |

## Related docs

- [NFM-3447](/NFM/issues/NFM-3447) — parent SRE-WARNING on disk pressure
- [NFM-3448](/NFM/issues/NFM-3448) — this issue
- [ADR-NFM-2139 §5 D1 / D2](/NFM/issues/NFM-2149) — SHA-tag and
  pre-deploy-assert design that this tool supports
