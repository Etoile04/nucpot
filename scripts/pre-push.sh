#!/usr/bin/env bash
# pre-push hook: run TypeScript type-check before allowing push
# Install via: npm run prepare

set -euo pipefail

echo "🔍 Running TypeScript type-check (tsc --noEmit)..."

if npx tsc --noEmit; then
  echo "✅ Type-check passed. Pushing..."
  exit 0
else
  echo ""
  echo "❌ TypeScript type-check failed. Push blocked."
  echo "   Fix the errors above and try again."
  echo "   To bypass this check (not recommended): git push --no-verify"
  exit 1
fi
