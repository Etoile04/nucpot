---
name: nfm80-productivity-review
description: Agent productivity review protocol - high-churn blocking decision
metadata:
  type: feedback
---

## NFM-80: High Churn Review Protocol

### Issue
Release Engineer assigned to NFM-41 (blog deployment) triggered high-churn alert (14 runs/6h, 10 runs/1h).

### Root Cause
DNS CNAME record deleted post-deployment, making `nucpot.dpdns.org` inaccessible. Agent correctly diagnosed issue but could not fix - DNS infrastructure is outside agent scope.

### Decision Pattern
**Block with named unblock owner** - NOT:
- ❌ Close as productive (agent not productive, stuck in loop)
- ❌ Snooze (would waste more money)
- ❌ Stop/cancel (deployment succeeded, only DNS broken)

**Why**: Agent permissions do not include infrastructure changes. Blocking prevents wasted compute ($0.40-$0.63/run) on unfixable verification loops.

### Unblock Owner
CEO (infrastructure owner) - only they can restore DNS records.

### Cost Impact
- Churn: 14 runs × ~$0.50 = ~$7 wasted
- Session caching inflates costs on repeated verification attempts
- Blocking immediately saves future waste

### Protocol
1. Detect high-churn pattern (10/1h or 30/6h runs)
2. Review latest runs and comments
3. Identify root cause (permission boundary vs. actual issue)
4. If outside agent scope: BLOCK with unblock owner
5. If within scope: decompose, reroute, or stop
6. Document decision on review issue

### Agent Permission Boundaries
Release Engineer: deployment, verification, monitoring - NOT infrastructure (DNS, domains, certificates).
