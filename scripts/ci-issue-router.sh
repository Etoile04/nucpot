#!/usr/bin/env bash
# CI Issue Router: GitHub CI issues → Paperclip SRE Monitor
# Scans GitHub issues with ci-failure/deploy-failure labels and routes them to Paperclip.
# Closes Paperclip issues when GitHub issues are closed.
set -euo pipefail

env -u PYTHONPATH python3 "$(dirname "$0")/ci-issue-router.py"
