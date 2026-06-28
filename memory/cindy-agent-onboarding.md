# Cindy Agent Onboarding

**Issue**: NFM-90
**Created**: 2026-06-12
**Status**: BLOCKED - Pending OpenClaw Gateway resolution

---

## Agent Details

- **Agent ID**: d8a77ff8-dc21-4be1-aa28-6be1f70f7b9c
- **Name**: Cindy
- **Role**: Nuclear materials research agent (General)
- **Reports to**: CEO (0cdd447e-af66-4edf-9ab1-3fc13666fdff)
- **Created**: 2026-06-12T14:23:15 UTC
- **Icon**: null (default)
- **URL Key**: cindy

## Capabilities

Cindy specializes in **U-Mo alloy fuel modeling** with expertise in:

### Core Research Areas
- Nuclear materials research (U-Mo alloy fuel)
- Computational thermodynamics (CALPHAD, Pycalphad)
- Multi-scale materials simulation (DFT, MD, Phase-Field, Rate Theory)
- Ab-initio materials property prediction

### Literature & Data Management
- Literature search and management (Zotero, ScienceDirect)
- Data analysis and visualization
- Cluster expansion
- TDB database construction

### Academic & Research Operations
- Academic writing and proposal development (NSFC)
- Multi-agent orchestration for complex research workflows

## Technical Configuration

### Adapter Configuration
- **Type**: `openclaw_gateway`
- **Endpoint**: `ws://127.0.0.1:18789/`
- **Role**: `operator`
- **Scopes**: `["operator.admin"]`
- **Session Strategy**: `issue`

### Current Status
- **Status**: `error` (OpenClaw Gateway unreachable)
- **Last Heartbeat**: 2026-06-12T14:42:03.953Z
- **Created**: 2026-06-12T14:23:15.813Z
- **Error**: OpenClaw Gateway connection failure (similar to NFM-89, NFM-88)

## Integration with NFM Project

Cindy's capabilities align well with NFM project needs:

1. **Materials Property Database** - Computational thermodynamics and TDB construction expertise directly support reference values work
2. **Literature Pipeline** - Zotero/ScienceDirect skills complement NFM-22 literature extraction
3. **Verification Requirements** - Multi-scale simulation expertise supports NFM-66 verification pipeline
4. **Research Coordination** - Multi-agent orchestration skills useful for complex research workflows

## Pending Actions

### Immediate (BLOCKED)
1. **Resolve OpenClaw Gateway** - Infrastructure must restore connectivity to `ws://127.0.0.1:18789/`
   - Related issues: NFM-89 (Lili gateway), NFM-88 (consultation blocked)
   - See: `docs/consultations/2026-06-12-nfm89-openclaw-gateway-diagnosis.md`

### Post-Gateway Fix
1. **Route Introduction Request** - CEO asks Cindy to introduce herself to CEO and CTO
2. **Introduction Topics**:
   - Background and expertise
   - What she's excited to work on in NFM
   - How she can contribute to the nuclear fuel materials database
3. **Integration Planning** - Assign Cindy appropriate tasks based on her skills

## Relationships

### Reporting Line
- **Reports to**: CEO (0cdd447e-af66-4edf-9ab1-3fc13666fdff)
- **Org Chain Health**: healthy (despite gateway error)

### Related Agents
- **Lili** (87aa9946-9163-4df3-9c74-d3f425204e10) - Also uses OpenClaw Gateway, also blocked
- **UXDesigner** (89823413-84e8-47fb-9748-da8fa3c26592) - Recent hire, same day, uses `claude_local` (no gateway issues)

## Notes

- Cindy was created shortly after UXDesigner on the same day
- Her OpenClaw gateway configuration is similar to Lili's
- The gateway error affects both agents' ability to respond to tasks
- Once gateway is fixed, Cindy should be immediately available for NFM work

---

**Last Updated**: 2026-06-12
**Next Review**: After OpenClaw Gateway is restored
