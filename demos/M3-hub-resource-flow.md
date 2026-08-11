# M3 Hub-Resource Flow Demo Script

> **Contract refs:** §3.1.2 (数据汇交模块), §5.8 (离线队列), §5.9 (同步验证)
> **Roadmap refs:** v1.7 §7.9.2 W10, §12 R11 Plan B
> **Duration target:** 8-12 min
> **Topology:** Plan B — 1 hub + 2 resource nodes

---

## Prerequisites

| Item | Value |
|------|-------|
| Docker Compose | `docker compose -f docker-compose.e2e.yml` |
| Hub UI | `http://localhost:8000/docs` (Swagger) |
| Admin Dashboard | `http://localhost:3000/admin/hub` |
| DB | PostgreSQL 16 (managed by compose) |

```bash
# Start the Plan B topology
docker compose -f docker-compose.e2e.yml up --build -d

# Verify all services healthy
docker compose -f docker-compose.e2e.yml ps
```

Expected output — 4 services `running (healthy)`:
- `nucpot-e2e-hub` (port 8000)
- `nucpot-e2e-resource-alpha` (computing)
- `nucpot-e2e-resource-beta` (storage)
- `nucpot-e2e-db`

---

## Step 1 — Show 1 Hub + 2 Resource Nodes Topology

**Narrator:** "NFMD uses a 1+N distributed architecture. One central hub coordinates multiple resource nodes. For this demo we run Plan B — the minimum viable topology with two resource nodes."

**Actions:**
1. Open the admin dashboard at `http://localhost:3000/admin/hub`
2. Show the live node list table — two registered nodes visible
3. Point out the columns: name, type (计算/存储), status badge, last heartbeat

**What to highlight:**
- `plan-b-alpha` — type: **计算** (computing) — status: **Active** (green badge)
- `plan-b-beta` — type: **存储** (storage) — status: **Active** (green badge)
- The 10-second auto-refresh polling indicator in the table header
- Heartbeat timestamps update in real time

**Expected screenshot:** Admin dashboard table with 2 rows, both showing green "Active" badges.

---

## Step 2 — Register a New Resource Node

**Narrator:** "Resource nodes register with the hub through a single API call. The hub assigns each node a unique ID and begins tracking its heartbeat."

**Option A — Via Admin Dashboard (UI):**
1. Click the "注册节点" button (top-right of the table)
2. Fill in the registration modal:
   - **节点名称:** `plan-b-gamma`
   - **节点类型:** `观测` (observatory)
   - **API 端点:** `http://plan-b-gamma:8000`
3. Click "确认" to submit
4. Show the success notification — new row appears in the table

**Option B — Via API (Swagger):**
1. Open `http://localhost:8000/docs`
2. Navigate to `POST /api/v1/hub/nodes/register`
3. Click "Try it out" and fill the request body:

```json
{
  "hub_node_id": "b1000000-0000-0000-0000-000000000001",
  "name": "plan-b-gamma",
  "node_type": "observatory",
  "api_endpoint": "http://plan-b-gamma:8000"
}
```

4. Execute and show the `201` response with the assigned `node_id`

**What to highlight:**
- Registration is a single atomic operation
- The hub returns a structured `ResourceNodeRegistration` with `node_id`, `name`, `node_type`, `api_endpoint`
- Heartbeat tracking begins automatically after registration

---

## Step 3 — Upload Data from Resource Node (DNA + Classification)

**Narrator:** "Each data upload automatically generates a DNA fingerprint — a cryptographic hash chain (SHA-256 + SM3) that provides data integrity and provenance. Classification levels enforce access control per security policy."

**Actions — using the Node Client SDK:**

```python
import asyncio
from nfm_node_client import Credentials, NfmNodeClient, NodeType

async def upload_demo_data():
    creds = Credentials(token="my-hub-token")
    client = NfmNodeClient(
        hub_url="http://localhost:8000",
        credentials=creds,
    )

    # Upload with classification level
    session = await client.upload(
        data=b"<measured nuclear fuel data payload>",
        metadata={
            "source": "plan-b-alpha",
            "material": "UO2",
            "enrichment_pct": 4.95,
            "classification": "非密",  # Unclassified
        },
        file_name="uo2-enrichment-4.95.csv",
        total_size=42,
        chunk_size=42,
    )
    print(f"session_id = {session.session_id}")

    # Verify DNA fingerprint was generated
    print(f"sha256 = {session.sha256_full}")

asyncio.run(upload_demo_data())
```

**What to highlight (AC-4: DNA, classification):**
1. **DNA fingerprint**: SHA-256 + SM3 hash chain is computed automatically on upload — no extra API call needed
2. **Classification enforcement**: The `classification` field maps to security levels (`非密`/`内部`/`秘密`). The `ClassificationGuard` service validates the level against policy before accepting the upload
3. **Resume token**: Each upload session gets a `resume_token` — if the upload is interrupted, it can resume from the last successful chunk
4. **Chunked upload**: Large files are split into configurable chunk sizes, tracked via `uploaded_chunks` / `total_chunks`

**Expected API response** (session object):
```json
{
  "session_id": "<uuid>",
  "resource_node_id": "<uuid>",
  "total_size": 42,
  "chunk_size": 42,
  "uploaded_chunks": 1,
  "total_chunks": 1,
  "sha256_full": "a3f2b8c1d4e5...",
  "classification_level": "非密",
  "resume_token": "<opaque-token>"
}
```

---

## Step 4 — Simulate Network Partition → Offline Operation

**Narrator:** "Network partitions are inevitable in distributed systems. When a resource node loses connectivity to the hub, it queues all operations locally in a SQLite-backed persistent queue. Data integrity is never compromised — work continues offline."

**Simulate partition using Docker network isolation:**

```bash
# Isolate resource-alpha from the hub (simulate partition)
docker network disconnect nucpot_default nucpot-e2e-resource-alpha
```

**Show offline behavior:**
1. Switch to the admin dashboard — `plan-b-alpha` status badge changes from green **Active** to red **Inactive** (heartbeat missed)
2. Attempt an upload from the isolated node — the operation is queued locally, not lost

```python
# This call succeeds locally even though hub is unreachable
await client.upload(
    data=b"offline measurement data",
    metadata={"source": "plan-b-alpha", "classification": "内部"},
    file_name="offline-payload.bin",
    total_size=21,
    chunk_size=21,
)
# Returns: queued in SQLite offline_queue
```

3. Check sync status on the isolated node:

```python
status = await client.get_sync_status()
# status.online == False
# status.pending_operations > 0
# status.offline_since == "<timestamp of partition>"
```

**What to highlight (AC-4: offline):**
- Operations are persisted to a local SQLite database — they survive process restarts
- Priority-based queuing: CREATE operations are queued ahead of UPDATE/DELETE
- The offline detector triggers on HTTP 5xx, timeouts, and connection-refused errors
- The admin dashboard reflects the partition in near-real time (10s poll)

---

## Step 5 — Reconnect → Sync Verification

**Narrator:** "When connectivity is restored, the sync engine replays all queued operations to the hub, then performs an incremental sync to pull any changes that happened on other nodes during the partition."

**Restore connectivity:**

```bash
# Reconnect resource-alpha to the network
docker network connect nucpot_default nucpot-e2e-resource-alpha
```

**Show sync in action:**
1. The node client automatically detects reconnection
2. Queued operations are pushed to the hub (CREATE operations replayed in order)
3. Incremental sync pulls any new records from the hub (created by `plan-b-beta` during the partition)

```python
# After reconnect, verify sync completed
status = await client.get_sync_status()
# status.online == True
# status.pending_operations == 0
# status.sync_watermark == "<timestamp of latest synced record>"
```

4. On the admin dashboard — `plan-b-alpha` badge returns to green **Active**, heartbeat resumes

**What to highlight (AC-4: sync, conflict resolution):**
- **Vector clocks**: Each record carries a vector clock that tracks causality across nodes
- **Conflict resolution**: When the same record was modified on both sides during partition, the sync engine detects the conflict via `ClockComparison.CONCURRENT` and resolves it using **Last-Write-Wins** (LWW) — the write with the latest timestamp wins
- **Bidirectional sync**: After reconnect, both push (local → hub) and pull (hub → local) run in sequence
- **Watermark tracking**: The `sync_watermark` records the last successfully synced point — resuming after interruption is deterministic

---

## Step 6 — Show Admin Dashboard with Sync Status

**Narrator:** "The admin dashboard provides a unified view of all nodes, their sync health, and recent activity. Let's walk through the key panels."

**Actions:**
1. Navigate to `http://localhost:3000/admin/hub`
2. Show the full node list table with all columns:
   - **节点名称** — node display name
   - **节点类型** — computing (计算) / storage (存储) / observatory (观测)
   - **状态** — Active (green) / Inactive (red) / Suspended (gray)
   - **最后心跳** — last heartbeat timestamp (formatted in zh-CN locale, or "从未上报" if never)
3. Click on `plan-b-alpha` to open the **Node Detail Drawer**:
   - Registration info (node ID, endpoint, type)
   - Sync metrics (watermark, pending operations)
   - Recent upload sessions with DNA fingerprints
4. Show the 10-second auto-refresh indicator — demonstrate that heartbeat updates appear without manual page refresh

**What to highlight (AC-4: all 5 contract criteria):**
- The dashboard is the single pane of glass for the 1+N architecture
- All 5 contract AC are visible from this panel:
  1. **DNA** — hash chain shown per upload session
  2. **Classification** — security level displayed per record
  3. **Resume** — resume tokens shown in upload session details
  4. **Offline** — node status badges reflect partition state
  5. **Sync** — watermark and pending operation counts track sync progress

---

## Cleanup

```bash
# Tear down the Plan B topology
docker compose -f docker-compose.e2e.yml down -v
```

---

## Demo Recording Notes

- **Resolution:** 1440x900 (desktop), with 390x844 mobile inset for responsive check
- **Pacing:** ~90s per step, 60s for cleanup
- **Transitions:** Use terminal split-screen — left panel for Docker/CLI, right panel for browser (admin dashboard + Swagger)
- **Annotations:** Overlay step numbers and contract AC callouts as lower-thirds
- **Audio:** Narrate in zh-CN; technical terms (DNA, classification, vector clock) spoken in English

---

## AC Mapping

| Demo Step | AC | Contract Ref |
|-----------|-----|-------------|
| Step 1 | AC-2 (Plan B topology) | §12 R11 |
| Step 2 | AC-1 (full script coverage) | — |
| Step 3 | AC-4 (DNA + classification) | §3.1.2 |
| Step 4 | AC-4 (offline) | §5.8 |
| Step 5 | AC-4 (sync) | §5.9 |
| Step 6 | AC-4 (dashboard) | §3.1.2 |
| All | AC-3 (clean E2E flow) | — |
