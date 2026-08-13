#!/bin/bash
# NFMDI GitHub repo 清理脚本
# 用法: bash cleanup-branches.sh
set -e
cd "$(git rev-parse --show-toplevel)"

echo "=== NFMDI Repo 清理脚本 ==="
echo ""

# ========== 1. 本地陈旧分支清理 ==========
echo "--- 1. 删除本地陈旧分支（CI 临时 + 已删远程）---"
BRANCHES_TO_DELETE=(
  NFM-1469-ci-failed-nucpot-ci-failure-s-2-workflow-s-need-attention
  NFM-1471-ci-failed-nucpot-ci-failure-s-2-workflow-s-need-attention
  NFM-1472-ci-failed-nucpot-ci-failure-s-4-workflow-s-need-attention
  NFM-1473-ci-failed-nucpot-ci-failure-s-1-workflow-s-need-attention
  NFM-1491-sre-github-184-ci-auto-e2e-failed-0bfc059
  NFM-1495-sre-github-185-ci-auto-e2e-failed-1a8556c
  NFM-1497-ci-failed-nucpot-ci-failure-s-2-workflow-s-need-attention
  NFM-1498-sre-github-186-ci-auto-backend-e2e-failed-f5befa7
  NFM-1501-ci-failed-nucpot-ci-failure-s-2-workflow-s-need-attention
  NFM-1502-ci-failed-nucpot-ci-failure-s-2-workflow-s-need-attention
  NFM-1504-ci-failed-nucpot-ci-failure-s-2-workflow-s-need-attention
  NFM-1505-ci-failed-nucpot-ci-failure-s-6-workflow-s-need-attention
  NFM-1506-ci-failed-nucpot-ci-failure-s-1-workflow-s-need-attention
  NFM-1507-ci-failed-nucpot-ci-failure-s-3-workflow-s-need-attention
  NFM-1507-format-fix
  NFM-1508-sre-github-193-ci-auto-backend-e2e-failed-08466a9
  NFM-1510-sre-github-197-ci-auto-backend-e2e-failed-1c0e09e
  NFM-1511-sre-github-198-ci-auto-e2e-failed-23e9881
  feat/nfm-1485-pdf-doi-kg-pipeline
  feat/nfm-kg-pipeline
  fix/nfm-1498-ci-lint
  fix/nfm-1499-graphbuilder-uuid
  fix/nfm-1501-ci-lint-merge-1498
  fix/nfm-1502-mypy-fixes
  fix/nfm-1504-ci-lint
)

deleted=0
for branch in "${BRANCHES_TO_DELETE[@]}"; do
  if git branch -D "$branch" 2>/dev/null; then
    echo "  ✅ $branch"
    deleted=$((deleted + 1))
  fi
done
echo "  共删除 $deleted 个本地分支"
echo ""

# ========== 2. Prune 远程跟踪 ==========
echo "--- 2. 清理远程跟踪引用 ---"
git fetch --prune origin 2>&1 | grep -E "deleting|->" || echo "  无需清理"
echo ""

# ========== 3. 剩余状态 ==========
echo "--- 3. 剩余分支 ---"
echo "本地:"
git branch
echo ""
echo "远程:"
git branch -r | grep -v HEAD
echo ""

# ========== 4. blog 测试文件（如果还没删） ==========
echo "--- 4. 检查 blog 测试文件 ---"
BLOG_TEST=$(find apps/api/content/blog/ -name "*.md" 2>/dev/null | wc -l)
if [ "$BLOG_TEST" -gt 0 ]; then
  echo "  发现 $BLOG_TEST 个测试文件，删除..."
  rm -f apps/api/content/blog/*.md
  echo "  ✅ 已删除"
else
  echo "  ✅ 已清理（无残留）"
fi
echo ""

echo "=== 清理完成 ==="
echo "保留: main + NFM-1512-plan-v1-6-sprint-4-5"
echo "保留远程: main + NFM-1497(PR#187) + NFM-1507(PR#196) + fix/nfm-1501(PR#189)"
