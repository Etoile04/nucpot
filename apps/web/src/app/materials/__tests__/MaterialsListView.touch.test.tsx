/**
 * NFM-4085 (C) — Touch swipe pagination on /materials.
 *
 * The list view binds touchstart/touchend to its content container and
 * treats a horizontal swipe as "next page" (left) or "previous page"
 * (right). Vertical-dominant or short swipes are ignored. Boundary
 * pages do not respond. A short toast "第 N 页" confirms the action.
 *
 * Tests:
 *   - Pure classifySwipe helper covers the gesture matrix.
 *   - Integration tests simulate touch events on the swipe area and
 *     assert the page state + mock antd message toast.
 *
 * Desktop zero-impact: the test suite already exercises mouse-driven
 * pagination/filter clicks; the touch handlers are no-ops when no
 * touch events are dispatched.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
// @vitest-environment jsdom
import {
  render,
  screen,
  waitFor,
  fireEvent,
  act,
} from "@testing-library/react";

// ── Mocks ──────────────────────────────────────────────────────────────

const mockRequest = vi.fn();

vi.mock("@/lib/api-client", () => ({
  request: (...args: unknown[]) => mockRequest(...args),
}));

let mockQueryString = "";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mockQueryString),
  usePathname: () => "/materials",
}));

// Mock antd message so we can assert the toast payload without rendering
// a real notification (which would require App context + a portal root).
const mockMessageInfo = vi.fn();

vi.mock("antd", async (importOriginal) => {
  const mod = await importOriginal<typeof import("antd")>();
  return {
    ...mod,
    message: {
      ...mod.message,
      info: (...args: unknown[]) => mockMessageInfo(...args),
    },
  };
});

import { MaterialsListView } from "../MaterialsListView";

// ── Helpers ────────────────────────────────────────────────────────────

function makeMaterials(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    id: `mat-${String(i + 1).padStart(3, "0")}`,
    name: `Mat ${String(i + 1).padStart(3, "0")}`,
    formula: null,
    crystal_structure: null,
    description: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  }));
}

function mockBigPage() {
  // 40 materials at page_size=20 → 2 pages of pagination.
  const materials = makeMaterials(40);
  mockRequest.mockImplementation((endpoint: string) => {
    if (endpoint === "/api/v1/material-categories") {
      return Promise.resolve({ success: true, data: { items: [] } });
    }
    if (endpoint === "/api/v1/material-categories/uncategorized-count") {
      return Promise.resolve({ success: true, data: { count: 0 } });
    }
    return Promise.resolve({
      success: true,
      data: { items: materials, total: 40, page: 1, per_page: 20 },
    });
  });
}

/**
 * Fire a touchstart → touchend pair with the given delta. Uses the
 * jsdom-shaped TouchEvent init that React 18 + testing-library expects
 * (a touches/changedTouches array with clientX/clientY).
 */
function fireSwipe(
  container: HTMLElement,
  startX: number,
  startY: number,
  endX: number,
  endY: number,
) {
  fireEvent.touchStart(container, {
    touches: [{ clientX: startX, clientY: startY }],
    changedTouches: [{ clientX: startX, clientY: startY }],
  });
  fireEvent.touchEnd(container, {
    touches: [],
    changedTouches: [{ clientX: endX, clientY: endY }],
  });
}

async function flushDebounce() {
  // Wait out the 300ms debounce + a buffer using real timers. Using
  // real timers throughout avoids the trap where switching between
  // fake and real mid-test destroys pending setTimeouts (the swipe
  // path schedules a fresh debounce timer after `setPage`, which
  // must remain live across the flush).
  await act(async () => {
    await new Promise((r) => setTimeout(r, 350));
  });
}

// ── classifySwipe (pure) tests ────────────────────────────────────────
//
// The helper is exported indirectly via the component; we re-implement
// the same predicate here so the unit matrix is locked independently.
// If the component's inline predicate drifts, the integration tests
// below will still fail — but the unit tests here document the rule.

function classifySwipe(deltaX: number, deltaY: number): "left" | "right" | null {
  const absX = Math.abs(deltaX);
  const absY = Math.abs(deltaY);
  if (absX < 50) return null;
  if (absY > 30) return null;
  return deltaX < 0 ? "left" : "right";
}

describe("classifySwipe (pure helper)", () => {
  it("classifies a clear left swipe (>= 50px horizontal, <= 30px vertical)", () => {
    expect(classifySwipe(-60, 5)).toBe("left");
    expect(classifySwipe(-120, 0)).toBe("left");
    expect(classifySwipe(-50, -30)).toBe("left");
  });

  it("classifies a clear right swipe", () => {
    expect(classifySwipe(60, 5)).toBe("right");
    expect(classifySwipe(120, 0)).toBe("right");
    expect(classifySwipe(50, -30)).toBe("right");
  });

  it("rejects swipes that are too short horizontally", () => {
    expect(classifySwipe(-49, 5)).toBeNull();
    expect(classifySwipe(0, 0)).toBeNull();
    expect(classifySwipe(20, 10)).toBeNull();
  });

  it("rejects swipes that are too vertical", () => {
    expect(classifySwipe(-100, 31)).toBeNull();
    expect(classifySwipe(-100, -50)).toBeNull();
    expect(classifySwipe(100, 100)).toBeNull();
  });

  it("rejects when both horizontal and vertical exceed thresholds (vertical wins)", () => {
    // 60px horizontal but 60px vertical — vertical-dominant
    expect(classifySwipe(60, 60)).toBeNull();
  });
});

// ── Integration tests ─────────────────────────────────────────────────

describe("MaterialsListView touch swipe pagination (NFM-4085 C)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockQueryString = "";
    mockMessageInfo.mockClear();
  });

  it("renders a swipe-area data-testid on the list container", async () => {
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();
    expect(
      screen.getByTestId("materials-list-swipe-area"),
    ).toBeInTheDocument();
  });

  it("swipe left on the swipe area increments the page", async () => {
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();

    const swipeArea = screen.getByTestId("materials-list-swipe-area");
    // Start on page 1, swipe left (deltaX = -80, deltaY = +10) → page 2.
    await act(async () => {
      fireSwipe(swipeArea, 200, 300, 120, 310);
    });
    await flushDebounce();

    const calls = mockRequest.mock.calls.map((c) => String(c[0]));
    const lastListCall = [...calls]
      .reverse()
      .find((c) => c.startsWith("/api/v1/materials?"));
    expect(lastListCall).toBeDefined();
    expect(lastListCall).toContain("page=2");
  });

  it("swipe right on the swipe area decrements the page", async () => {
    // Start on page 2 so swipe right (→ page 1) is meaningful.
    mockQueryString = "page=2";
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();

    const swipeArea = screen.getByTestId("materials-list-swipe-area");
    await act(async () => {
      fireSwipe(swipeArea, 100, 300, 200, 305);
    });
    await flushDebounce();

    const calls = mockRequest.mock.calls.map((c) => String(c[0]));
    const lastListCall = [...calls]
      .reverse()
      .find((c) => c.startsWith("/api/v1/materials?"));
    expect(lastListCall).toBeDefined();
    expect(lastListCall).toContain("page=1");
  });

  it("swipe left at the last page is a no-op (no request, no toast)", async () => {
    // 40 items at page_size=20 → page 2 is the last page.
    mockQueryString = "page=2";
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();

    const callsBefore = mockRequest.mock.calls.length;
    const swipeArea = screen.getByTestId("materials-list-swipe-area");
    await act(async () => {
      fireSwipe(swipeArea, 200, 300, 120, 305);
    });
    await flushDebounce();

    // No new /materials list request should have been fired after the swipe.
    const postSwipeCalls = mockRequest.mock.calls.slice(callsBefore);
    const listCalls = postSwipeCalls
      .map((c) => String(c[0]))
      .filter((e) => e.startsWith("/api/v1/materials?"));
    expect(listCalls).toHaveLength(0);
    expect(mockMessageInfo).not.toHaveBeenCalled();
  });

  it("swipe right at the first page is a no-op (no request, no toast)", async () => {
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();

    const callsBefore = mockRequest.mock.calls.length;
    const swipeArea = screen.getByTestId("materials-list-swipe-area");
    await act(async () => {
      fireSwipe(swipeArea, 100, 300, 200, 305);
    });
    await flushDebounce();

    const postSwipeCalls = mockRequest.mock.calls.slice(callsBefore);
    const listCalls = postSwipeCalls
      .map((c) => String(c[0]))
      .filter((e) => e.startsWith("/api/v1/materials?"));
    expect(listCalls).toHaveLength(0);
    expect(mockMessageInfo).not.toHaveBeenCalled();
  });

  it("vertical-dominant swipe does not change the page", async () => {
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();

    const callsBefore = mockRequest.mock.calls.length;
    const swipeArea = screen.getByTestId("materials-list-swipe-area");
    // deltaX = -80 (would be a left swipe), deltaY = +80 (too vertical).
    await act(async () => {
      fireSwipe(swipeArea, 200, 200, 120, 280);
    });
    await flushDebounce();

    const postSwipeCalls = mockRequest.mock.calls.slice(callsBefore);
    const listCalls = postSwipeCalls
      .map((c) => String(c[0]))
      .filter((e) => e.startsWith("/api/v1/materials?"));
    expect(listCalls).toHaveLength(0);
    expect(mockMessageInfo).not.toHaveBeenCalled();
  });

  it("short horizontal swipe does not change the page", async () => {
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();

    const callsBefore = mockRequest.mock.calls.length;
    const swipeArea = screen.getByTestId("materials-list-swipe-area");
    // deltaX = -30, deltaY = +5 → too short.
    await act(async () => {
      fireSwipe(swipeArea, 200, 300, 170, 305);
    });
    await flushDebounce();

    const postSwipeCalls = mockRequest.mock.calls.slice(callsBefore);
    const listCalls = postSwipeCalls
      .map((c) => String(c[0]))
      .filter((e) => e.startsWith("/api/v1/materials?"));
    expect(listCalls).toHaveLength(0);
    expect(mockMessageInfo).not.toHaveBeenCalled();
  });

  it("toast shows '第 N 页' after a valid swipe", async () => {
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();

    mockMessageInfo.mockClear();
    const swipeArea = screen.getByTestId("materials-list-swipe-area");
    await act(async () => {
      fireSwipe(swipeArea, 200, 300, 120, 310);
    });
    await flushDebounce();

    expect(mockMessageInfo).toHaveBeenCalledTimes(1);
    const toastText = String(mockMessageInfo.mock.calls[0]?.[0] ?? "");
    expect(toastText).toContain("第 2 页");
  });

  it("desktop mouse click on pagination still works (zero regression on swipe-area presence)", async () => {
    // Desktop zero-impact: the swipe-area only reacts to touch events;
    // a mouse click on the antd pagination must still trigger a page
    // change. This guards against an accidental CSS rule that would
    // swallow mouse clicks on the table area.
    mockQueryString = "";
    mockBigPage();
    render(<MaterialsListView />);
    await flushDebounce();

    const callsBefore = mockRequest.mock.calls.length;
    const page2 = await waitFor(() =>
      document.querySelector<HTMLLIElement>(".ant-pagination-item-2"),
    );
    await act(async () => {
      fireEvent.click(page2!);
    });
    await flushDebounce();

    const postClickCalls = mockRequest.mock.calls.slice(callsBefore);
    const listCalls = postClickCalls
      .map((c) => String(c[0]))
      .filter((e) => e.startsWith("/api/v1/materials?"));
    expect(listCalls.length).toBeGreaterThanOrEqual(1);
    const last = listCalls[listCalls.length - 1];
    expect(last).toContain("page=2");
  });
});
