#!/usr/bin/env bash
#
# Performance Testing Setup Script
#
# Quick script to set up and run performance tests for MD Verification API.
# Run with: ./run-performance-test.sh [users] [duration_minutes]
#

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
USERS=${1:-20}              # Default 20 users
DURATION=${2:-5}              # Default 5 minutes
HOST=${3:-"http://localhost:8000"}

echo -e "${GREEN}=== MD Verification Performance Testing ===${NC}"
echo "Users: $USERS"
echo "Duration: $DURATION minutes"
echo "Host: $HOST"
echo ""

# Check if API server is running
echo -e "${YELLOW}Checking if API server is running...${NC}"
if curl -s "$HOST/api/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ API server is running${NC}"
else
    echo -e "${RED}✗ API server is not running at $HOST${NC}"
    echo "Please start the API server first:"
    echo "  cd apps/api"
    echo "  uv run uvicorn src.main:app --reload"
    exit 1
fi

# Install performance dependencies
echo -e "${YELLOW}Installing performance dependencies...${NC}"
cd apps/api
uv sync --extra performance --quiet

echo -e "${GREEN}✓ Dependencies installed${NC}"

# Check if performance extras are available
echo -e "${YELLOW}Checking performance testing dependencies...${NC}"
if uv run python -c "import locust" 2>/dev/null; then
    echo -e "${GREEN}✓ Locust is available${NC}"
else
    echo -e "${RED}✗ Locust is not installed${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}Starting performance test...${NC}"
echo "Web UI will be available at: http://localhost:8089"
echo ""

# Convert duration to seconds
DURATION_SECONDS=$((DURATION * 60))

# Run Locust
uv run --with performance locust \
    -f tests/performance/locustfile.py \
    --users "$USERS" \
    --spawn-rate 2 \
    --run-time "$DURATION_SECONDS" \
    --host "$HOST" \
    --html performance_report_$(date +%Y%m%d_%H%M%S).html \
    --csv perf_results_$(date +%Y%m%d_%H%M%S)

echo ""
echo -e "${GREEN}=== Performance Test Complete ===${NC}"
echo "Results saved to:"
echo "  - HTML report: performance_report_*.html"
echo "  - CSV data: perf_results_*.csv"
echo ""
echo "Open the HTML report in your browser to view detailed results."