#!/usr/bin/env bash
#
# Security Scan Script
#
# Scans codebase for security vulnerabilities:
# - Hardcoded secrets (API keys, passwords, tokens)
# - Credential exposure
# - Insecure configurations
# - OWASP Top 10 vulnerabilities
#

set -e
export LC_ALL=C.UTF-8

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Security Scan for NFMD ===${NC}"
echo ""

# Security patterns to search for
PATTERNS=(
  "sk-proj-[a-zA-Z0-9]{32,}"           # OpenAI API keys
  "AKIA[0-9A-Z]{16}"                   # AWS Access Keys
  "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^[:space:]]+"  # Email:password
  "password[\"']?\s*[:=]\s*[\"']?[a-zA-Z0-9]+"  # Password assignments
  "api[_-]?key[\"']?\s*[:=]\s*[\"']?[a-zA-Z0-9\-]+"  # API keys
  "secret[\"']?\s*[:=]\s*[\"']?[a-zA-Z0-9]+"     # Secrets
  "token[\"']?\s*[:=]\s*[\"']?[a-zA-Z0-9.\-_]+"     # Tokens
  "BEGIN RSA PRIVATE KEY"             # SSH keys
  "BEGIN PRIVATE KEY"                 # Private keys
  "mysql:\/\/[a-zA-Z0-9:]+@[^[:space:]]+" # MySQL connection strings
  "postgresql:\/\/[^[:space:]]+:[^[:space:]]+@" # PostgreSQL connection strings
  "mongodb:\/\/[^[:space:]]+"            # MongoDB connection strings
)

# Files to exclude from scan
EXCLUDE_DIRS=(
  "node_modules"
  ".git"
  ".venv"
  "venv"
  "__pycache__"
  ".pytest_cache"
  "dist"
  "build"
  ".next"
  "coverage"
)

EXCLUDE_FILES=(
  "*.min.js"
  "*.min.css"
  "*.lock"
  "*.pyc"
  "*.map"
  "package-lock.json"
  "yarn.lock"
  "pnpm-lock.yaml"
  ".gitignore"
  "security-scan.sh"
  "eslint.config"
)

ISSUES_FOUND=0

echo -e "${YELLOW}Scanning for hardcoded secrets...${NC}"

# Scan for each pattern
for pattern in "${PATTERNS[@]}"; do
  echo -e "${BLUE}Checking: $pattern${NC}"

  # Build grep command with exclusions
  exclude_args=""
  for dir in "${EXCLUDE_DIRS[@]}"; do
    exclude_args="$exclude_args --exclude-dir=$dir"
  done
  for file in "${EXCLUDE_FILES[@]}"; do
    exclude_args="$exclude_args --exclude=$file"
  done

  # Run grep
  matches=$(grep -r -n "$pattern" apps/ $exclude_args 2>/dev/null || true)

  if [ -n "$matches" ]; then
    echo -e "${RED}!! FOUND:${NC}"
    echo "$matches"
    ISSUES_FOUND=$((ISSUES_FOUND + 1))
  fi
done

echo ""
echo -e "${YELLOW}Scanning for exposed credentials...${NC}"

# Check for common credential files
CREDENTIAL_FILES=(
  ".env"
  ".env.local"
  ".env.production"
  "credentials.json"
  "secrets.json"
  "config/secrets.ts"
  "config/secrets.js"
)

for cred_file in "${CREDENTIAL_FILES[@]}"; do
  if [ -f "$cred_file" ]; then
    # Check if file is in .gitignore
    if grep -q "$cred_file" .gitignore 2>/dev/null; then
      echo -e "${GREEN}OK $cred_file (in .gitignore)${NC}"
    else
      echo -e "${RED}!! WARNING: $cred_file not in .gitignore${NC}"
      ISSUES_FOUND=$((ISSUES_FOUND + 1))
    fi

    # Check if file has restrictive permissions
    perms=$(stat -f "%Lp" "$cred_file" 2>/dev/null || stat -f "%A" "$cred_file")
    if [ "$perms" != "600" ] && [ "$perms" != "400" ]; then
      echo -e "${YELLOW}  !! File permissions: $perms (recommend 600)${NC}"
    fi
  fi
done

echo ""
echo -e "${YELLOW}Scanning for debug console.log statements...${NC}"

# Check for console.log in source code (excluding node_modules)
console_logs=$(grep -r "console\.log" apps/web/src apps/api/src 2>/dev/null || true)

if [ -n "$console_logs" ]; then
  echo -e "${YELLOW}!! Found console.log statements (should use proper logging):${NC}"
  echo "$console_logs"
  # Count occurrences
  count=$(echo "$console_logs" | wc -l)
  echo -e "  Total: $count occurrences"
fi

echo ""
echo -e "${YELLOW}Checking for hardcoded test credentials...${NC}"

# Check test files for hardcoded credentials
test_creds=$(grep -r "password.*test\|test.*password" apps/web/e2e apps/api/tests 2>/dev/null || true)

if [ -n "$test_creds" ]; then
  echo -e "${GREEN}OK Found test credentials (acceptable in tests)${NC}"
  echo "$test_creds"
else
  echo -e "${BLUE}  No test credentials found${NC}"
fi

echo ""
echo -e "${YELLOW}Checking for SQL injection vulnerabilities...${NC}"

# Look for unsafe SQL patterns
unsafe_sql=$(grep -rE "SELECT.*FROM.*WHERE.*['\"]|['\"]\s*\+\s*['\"]" apps/api/src 2>/dev/null || true)

if [ -n "$unsafe_sql" ]; then
  echo -e "${RED}!! Potential unsafe SQL construction:${NC}"
  echo "$unsafe_sql"
  echo "  → Use parameterized queries instead"
  ISSUES_FOUND=$((ISSUES_FOUND + 1))
fi

echo ""
echo -e "${YELLOW}Checking for XSS vulnerabilities...${NC}"

# Look for dangerous HTML/JS patterns
xss_patterns=(
  "dangerouslySetInnerHTML"
  "innerHTML.*user"
  "eval\("
  "Function\("
  "document\.write"
)

for pattern in "${xss_patterns[@]}"; do
  matches=$(grep -r "$pattern" apps/web/src 2>/dev/null || true)
  if [ -n "$matches" ]; then
    echo -e "${YELLOW}!! Found $pattern (review for XSS risk)${NC}"
    echo "$matches"
  fi
done

echo ""
echo -e "${BLUE}=== Security Scan Summary ===${NC}"

if [ $ISSUES_FOUND -eq 0 ]; then
  echo -e "${GREEN}OK No critical security issues found${NC}"
  echo -e "${GREEN}OK Security scan passed${NC}"
  exit 0
else
  echo -e "${RED}!! Found $ISSUES_FOUND potential security issues${NC}"
  echo -e "${RED}!! Please review and fix before deployment${NC}"
  exit 1
fi
