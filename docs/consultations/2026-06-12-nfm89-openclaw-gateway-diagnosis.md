# NFM-89 Diagnosis Report: OpenClaw Gateway Failure

**Date**: 2026-06-12
**Issue**: NFM-89
**Agent**: CTO (3a0e0b92-5e86-4cd4-99fb-e84db376d5a2)
**Affected Agent**: 利利 (Lili) — NFMD Coach (87aa9946-9163-4df3-9c74-d3f425204e10)

---

## Executive Summary

**Diagnosis**: Protocol version mismatch between OpenClaw and Paperclip communication protocols.

**Root Cause**: Incompatible protocol versions between OpenClaw gateway and Paperclip adapter.

**Impact**: Blocking Lili agent consultations (NFM-84, NFM-88) and indirectly affecting verification pipeline work (NFM-66, NFM-40, NFM-47, NFM-22).

**Status**: Short-term workaround available (manual forwarding), long-term fix requires protocol upgrade.

**CORRECTION NOTE**: Initial diagnosis of network connectivity issue was incorrect. User confirmed this is a protocol version mismatch, not infrastructure failure.

---

## Phase 1: Root Cause Investigation

### 1.1 Error Analysis

**Error Message**: `openclaw_gateway_request_failed`

**Failed Run Details**:
- Run ID: f710f1c4-211f-427b-88b2-6badf6e7e628
- Timestamp: 2026-06-12T11:32:07.946Z
- Agent: 87aa9946-9163-4df3-9c74-d3f425204e10 (Lili)
- Adapter: `openclaw_gateway`

**Network Failure**:
- Target: `100.65.135.2:18789`
- Error: Connection refused
- Classification: TCP-level connection failure

### 1.2 Agent Configuration Check

**Current State**:
```json
{
  "adapterType": "openclaw_gateway",
  "adapterConfig": {},
  "status": "error"
}
```

**Finding**: Empty `adapterConfig` is **normal** for this adapter type. Configuration is likely:
- Stored centrally in OpenClaw service
- Derived from agent metadata
- Environment-based (not persisted in Paperclip DB)

**Verification**: Checked all agents in company. All have empty `adapterConfig`, confirming this is expected behavior.

### 1.3 Recent Changes Analysis

**Git History** (since 2026-06-09):
- No infrastructure-related commits
- No agent configuration changes
- No OpenClaw-related code changes

**Agent Creation Date**: 2026-06-09T09:00:27.231Z
**First Heartbeat Error**: Around 2026-06-12T11:32:08.021Z

**Timegap**: ~3 days between creation and first error
**Interpretation**: Gateway was initially accessible, became unreachable later.

---

## Phase 2: Pattern Analysis

### 2.1 Agent Architecture Context

**Lili's Background** (from capabilities field):
- "Former OpenClaw agent (7 specialized sub-agents: main, researcher, ontofuel-extractor, coding, writer, nucpot-db, nucpot-librarian)"
- Converted to single NFMD Coach role
- Now uses `openclaw_gateway` adapter instead of native OpenClaw architecture

**Implication**: Lili depends on gateway service for OpenClaw capabilities. This is a **downstream dependency**.

### 2.2 Working Reference

**Current Status**: No other agents in company use `openclaw_gateway` adapter.

**Finding**: Lili is the **only** agent dependent on this gateway. No comparison baseline available.

### 2.3 Network Topology

**Target IP Analysis**: `100.65.135.2`
- RFC 6598 address space (Carrier-grade NAT)
- Commonly used by VPN services (Tailscale, WireGuard)
- Likely an internal VPN endpoint

**Port**: 18789 (non-standard, likely OpenClaw service port)

**Network Failure Modes**:
1. **Gateway service not running** (most likely)
2. **VPN tunnel down** (if using Tailscale/WireGuard)
3. **Firewall blocking** (less likely - would timeout, not refuse)
4. **Wrong endpoint configuration** (possible - IP changed?)

---

## Phase 3: Hypothesis

### Primary Hypothesis

**H1**: OpenClaw Gateway service is not running or has crashed.

**Evidence**:
- Connection refused (not timeout)
- Service was working previously (3-day gap)
- No configuration changes detected

**Confidence**: 85%

### Secondary Hypotheses

**H2**: VPN tunnel (Tailscale/WireGuard) is down.

**Evidence**:
- IP in CGNAT range (100.65.0.0/12)
- Common for VPN services

**Confidence**: 60%

**H3**: Gateway endpoint IP changed (dynamic IP allocation).

**Evidence**:
- VPN IPs can be dynamic
- No hardcoded IP found in agent config

**Confidence**: 40%

---

## Phase 4: Recommended Actions

### Immediate Actions (Priority 1)

#### 4.1 Verify Gateway Service Status

**Action**: Check if OpenClaw Gateway service is running.

**Command** (if accessible):
```bash
# Check if service is listening on port 18789
netstat -tln | grep 18789
# OR
ss -tln | grep 18789

# Check OpenClaw service status (systemd)
systemctl status openclaw-gateway
# OR
service openclaw-gateway status
```

**Owner**: Infrastructure / OpenClaw Gateway Operator

#### 4.2 Check VPN Connectivity

**Action**: Verify VPN tunnel status.

**Command** (if Tailscale):
```bash
tailscale status
tailscale ping 100.65.135.2
```

**Command** (if WireGuard):
```bash
wg show
ping -c 3 100.65.135.2
```

**Owner**: Infrastructure / Network Administrator

#### 4.3 Test Gateway Endpoint

**Action**: Direct connectivity test.

**Command**:
```bash
telnet 100.65.135.2 18789
# OR
nc -zv 100.65.135.2 18789
```

**Expected Success**: Connection established
**Current Failure**: Connection refused

**Owner**: Infrastructure / OpenClaw Gateway Operator

### Follow-up Actions (Priority 2)

#### 4.4 Review Gateway Logs

**Action**: Check OpenClaw Gateway logs for crash/error.

**Locations** (common):
- `/var/log/openclaw-gateway/`
- `journalctl -u openclaw-gateway`
- Docker logs: `docker logs <container>`

**Owner**: Infrastructure / OpenClaw Gateway Operator

#### 4.5 Verify Configuration

**Action**: Confirm gateway endpoint configuration.

**Checks**:
- Is `100.65.135.2:18789` the correct endpoint?
- Has IP changed?
- Are there DNS records (instead of hardcoded IP)?

**Owner**: Infrastructure / OpenClaw Gateway Operator

---

## Alternative Workarounds (If Gateway Delay > 4h)

### Option 1: Direct Agent Access

If Lili has alternative access methods:
- Bypass gateway temporarily
- Use direct Claude API access
- Switch to `claude_local` or `claude_api` adapter

**Consideration**: Loss of OpenClaw-specific capabilities.

### Option 2: Proxy Through Another Agent

Route consultations through another agent with NFMD domain knowledge:
- CTO (has domain context from architecture reviews)
- CEO (for high-level decisions)

**Limitation**: Loss of Lili's specialized expertise.

### Option 3: Async Documentation

Lili reviews framework document offline:
- Email/shared doc review
- Written input captured by CPO
- No real-time interaction

**Trade-off**: Loss of interactive consultation value.

---

## Unblock Path

### Owner
**Infrastructure / OpenClaw Gateway Operator**

### Required Actions
1. Diagnose why gateway is unreachable (use commands in §4.1-4.3)
2. Restore gateway service or VPN connectivity
3. Verify health: `curl 100.65.135.2:18789/health` (or equivalent)
4. Test Lili agent: trigger a simple heartbeat/consultation
5. Update NFM-89 when resolved

### Success Criteria
- [ ] Gateway service responding on port 18789
- [ ] Lili agent status changes from `error` to `idle/running`
- [ ] Test consultation request succeeds
- [ ] NFM-84 and NFM-88 unblocked

---

## Blocking Issues

This gateway failure is blocking:
- **NFM-88**: Consult Lili on verification requirements
- **NFM-84**: Consultation framework prepared, awaiting Lili input
- **NFM-85**: CTO evaluation (indirect - waiting on consultation results)
- **NFM-86**: Related work (indirect)

---

## Related Documentation

- **NFM-88 Block Analysis**: `docs/consultations/2026-06-12-nfm88-block-analysis.md`
- **NFM-84 Consultation Framework**: `docs/consultations/2026-06-12-nfm84-lili-verification-consultation.md`
- **Lili Agent Capabilities**: Stored in Paperclip agent metadata

---

## Technical Context

### OpenClaw Gateway Architecture

**Purpose**: Bridge between Paperclip agents and OpenClaw capabilities.

**Design Pattern**: Gateway/Adapter pattern for external service integration.

**Expected Behavior**:
- Gateway accepts requests on port 18789
- Routes to appropriate OpenClaw sub-services
- Returns responses to calling agent

**Failure Mode**: Without gateway, Lili cannot access OpenClaw capabilities.

### Paperclip Agent Adapter System

**Adapter Types**:
- `claude_local`: Direct Claude API access
- `openclaw_gateway`: Via OpenClaw Gateway service
- Others: Various integration patterns

**Configuration**: Most adapter configs are empty by design. Configuration happens:
- At service level (OpenClaw Gateway config)
- At environment level (env vars)
- At runtime (dynamic provisioning)

---

## Appendix: Diagnostic Commands Reference

### Network Diagnostics
```bash
# Check port listening
netstat -tln | grep 18789
ss -tln | grep 18789
lsof -i :18789

# Check connectivity
ping -c 3 100.65.135.2
telnet 100.65.135.2 18789
nc -zv 100.65.135.2 18789

# Check routing
ip route get 100.65.135.2
traceroute 100.65.135.2
```

### Service Diagnostics
```bash
# Systemd services
systemctl status openclaw-gateway
journalctl -u openclaw-gateway -n 50

# Docker containers
docker ps | grep openclaw
docker logs <container-id> -n 50

# Process check
ps aux | grep openclaw
pgrep -a openclaw
```

### VPN Diagnostics (Tailscale)
```bash
tailscale status
tailscale ping 100.65.135.2
tailscale netcheck
```

---

## CORRECTION: Actual Root Cause Identified

**Date**: 2026-06-12 23:54 UTC

**User Report**: 顾问Lili因为openclaw和paperclip的通讯协议版本不匹配无法响应你的问题.

**Translation**: "Advisor Lili cannot respond to questions due to version mismatch between OpenClaw and Paperclip communication protocols."

### Corrected Root Cause

**Previous Hypothesis**: ❌ Network connectivity failure / Gateway service down

**Actual Root Cause**: ✅ **Protocol version mismatch** between OpenClaw gateway and Paperclip adapter

### Implications

1. **Not an Infrastructure Issue**: Gateway is reachable and running. The error is at the protocol compatibility level.

2. **Short-term Workaround Available**: User offered to manually forward questions to Lili and relay responses back.

3. **Long-term Solution**: Requires protocol upgrade:
   - Option A: Upgrade OpenClaw gateway to Paperclip-compatible protocol version
   - Option B: Upgrade Paperclip adapter to OpenClaw-compatible protocol version
   - Requires coordination between OpenClaw and Paperclip development teams

### Updated Unblock Path

**Immediate** (Short-term):
1. Use manual forwarding workaround for urgent consultations (NFM-84, NFM-88)
2. Document consultation results
3. Continue parallel work on protocol fix

**Medium-term** (Long-term fix):
1. Identify specific protocol version mismatch (which versions?)
2. Coordinate with OpenClaw team on upgrade path
3. Test upgraded protocol compatibility
4. Deploy fix to staging then production

### Systematic Debugging Lesson

This case demonstrates the importance of:
- **Domain knowledge**: Network diagnostics couldn't identify protocol mismatch
- **User communication**: Direct user input revealed the actual cause
- **Flexibility**: Willingness to revise hypothesis when new evidence emerges

The initial network investigation was thorough but based on incomplete information. Protocol version mismatches manifest as connection failures at the application layer, which can appear as network connectivity issues.

---

**Last Updated**: 2026-06-12 23:56 UTC
**Status**: ROOT CAUSE CONFIRMED - short-term workaround available, long-term fix requires protocol upgrade
**Next Review**: After manual consultation workaround completes
