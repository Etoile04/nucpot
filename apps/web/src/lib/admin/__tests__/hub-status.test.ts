/**
 * Tests for hub-status.ts — node live-status derivation (NFM-2023).
 *
 * The Hub Admin UI shows 在线/离线/注册中 badges. The status is derived
 * client-side from the node's DB status + last_heartbeat freshness,
 * since the B2 API (NFM-2022) only stores the last heartbeat timestamp.
 */

import { describe, it, expect } from "vitest"

import {
  deriveNodeLiveStatus,
  HEARTBEAT_ONLINE_THRESHOLD_MS,
  LIVE_STATUS_META,
} from "@/lib/admin/hub-status"

const NOW = new Date("2026-07-30T12:00:00Z")

function nodeWith(overrides: {
  status?: string
  last_heartbeat?: string | null
  offline_since?: string | null
}) {
  return {
    status: overrides.status ?? "active",
    last_heartbeat:
      overrides.last_heartbeat === undefined ? null : overrides.last_heartbeat,
    offline_since:
      overrides.offline_since === undefined ? null : overrides.offline_since,
  }
}

describe("deriveNodeLiveStatus", () => {
  it("returns 'registering' when the node has never heartbeated", () => {
    expect(deriveNodeLiveStatus(nodeWith({ last_heartbeat: null }), NOW)).toBe(
      "registering",
    )
  })

  it("returns 'online' when the last heartbeat is within the threshold", () => {
    const fresh = new Date(NOW.getTime() - 30_000).toISOString()
    expect(
      deriveNodeLiveStatus(nodeWith({ last_heartbeat: fresh }), NOW),
    ).toBe("online")
  })

  it("returns 'online' exactly at the threshold boundary", () => {
    const boundary = new Date(
      NOW.getTime() - HEARTBEAT_ONLINE_THRESHOLD_MS,
    ).toISOString()
    expect(
      deriveNodeLiveStatus(nodeWith({ last_heartbeat: boundary }), NOW),
    ).toBe("online")
  })

  it("returns 'offline' when the last heartbeat is stale", () => {
    const stale = new Date(
      NOW.getTime() - HEARTBEAT_ONLINE_THRESHOLD_MS - 1_000,
    ).toISOString()
    expect(
      deriveNodeLiveStatus(nodeWith({ last_heartbeat: stale }), NOW),
    ).toBe("offline")
  })

  it("returns 'suspended' regardless of heartbeat freshness", () => {
    const fresh = new Date(NOW.getTime() - 1_000).toISOString()
    expect(
      deriveNodeLiveStatus(
        nodeWith({ status: "suspended", last_heartbeat: fresh }),
        NOW,
      ),
    ).toBe("suspended")
  })

  it("returns 'offline' for an inactive node even with a fresh heartbeat", () => {
    const fresh = new Date(NOW.getTime() - 1_000).toISOString()
    expect(
      deriveNodeLiveStatus(
        nodeWith({ status: "inactive", last_heartbeat: fresh }),
        NOW,
      ),
    ).toBe("offline")
  })

  it("treats an unparseable heartbeat timestamp as offline", () => {
    expect(
      deriveNodeLiveStatus(nodeWith({ last_heartbeat: "not-a-date" }), NOW),
    ).toBe("offline")
  })
})

describe("LIVE_STATUS_META", () => {
  it("maps every live status to a Chinese label and badge color", () => {
    expect(LIVE_STATUS_META.online.label).toBe("在线")
    expect(LIVE_STATUS_META.offline.label).toBe("离线")
    expect(LIVE_STATUS_META.registering.label).toBe("注册中")
    expect(LIVE_STATUS_META.suspended.label).toBe("已暂停")
    for (const meta of Object.values(LIVE_STATUS_META)) {
      expect(meta.color).toBeTruthy()
    }
  })
})
