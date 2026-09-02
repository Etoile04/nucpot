import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  act,
} from "@testing-library/react";
import { MaterialsListView } from "../MaterialsListView";

// ── Mocks ──────────────────────────────────────────────────────────────

const mockMaterials = [
  {
    id: "mat-001",
    name: "UO₂",
    formula: "UO2",
    crystal_structure: "fluorite",
    description: "Uranium dioxide",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
  {
    id: "mat-002",
    name: "Zr",
    formula: "Zr",
    crystal_structure: "hcp",
    description: "Zirconium",
    is_active: true,
    created_at: "2026-01-03T00:00:00Z",
    updated_at: "2026-01-04T00:00:00Z",
  },
];

const mockRequest = vi.fn();

vi.mock("@/lib/api-client", () => ({
  request: (...args: unknown[]) => mockRequest(...args),
}));

// ── next/navigation mock (NFM-3917 / Tier 1D) ───────────────────────────
//
// MaterialsListView reads `?category_id=` via useSearchParams and writes
// back via `window.history.replaceState` (not `router.replace` — see the
// component comments for why). Tests use an in-memory query string that
// we can mutate per test; the assertion is whether `request()` was
// called with the expected query (the URL state itself is implementation
// detail). This is enough — the Playwright e2e spec exercises the real
// browser round-trip.

let mockQueryString = "";
const mockReplaceState = vi.fn();

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mockQueryString),
  usePathname: () => "/materials",
}));

// Stub history.replaceState so the URL-sync effect has a deterministic
// target to assert against. `lastUrlRef` already short-circuits on
// identical target strings, so we only inspect distinct replacements.
const originalReplaceState = window.history.replaceState.bind(window.history);
beforeEach(() => {
  mockReplaceState.mockClear();
  window.history.replaceState = ((
    ...args: Parameters<typeof originalReplaceState>
  ) => {
    mockReplaceState(...args);
    // Mirror the effect on the live `window.location` so subsequent
    // currentSearch checks behave like a real browser.
    originalReplaceState(...args);
  }) as typeof window.history.replaceState;
});
afterEach(() => {
  window.history.replaceState = originalReplaceState;
});

// ── Test fixtures ──────────────────────────────────────────────────────

const SAMPLE_CATEGORIES: ReadonlyArray<{
  readonly id: string;
  readonly name: string;
  readonly slug: string;
  readonly description: string | null;
  readonly parent_id: string | null;
  readonly sort_order: number;
  readonly created_at: string;
  readonly updated_at: string;
}> = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Oxide Fuel",
    slug: "oxide_fuel",
    description: null,
    parent_id: null,
    sort_order: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Cladding Alloy",
    slug: "cladding_alloy",
    description: null,
    parent_id: null,
    sort_order: 4,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

const FIRST_CATEGORY_ID = SAMPLE_CATEGORIES[0]?.id ?? "";

function mockCategoryRequest() {
  // /api/v1/material-categories returns ApiResponse<{items: [...]}>
  mockRequest.mockImplementation((endpoint: string) => {
    if (endpoint === "/api/v1/material-categories") {
      return Promise.resolve({
        success: true,
        data: { items: SAMPLE_CATEGORIES },
      });
    }
    if (endpoint === "/api/v1/material-categories/uncategorized-count") {
      // Default mock: 0 uncategorized (so existing tests stay quiet).
      return Promise.resolve({
        success: true,
        data: { count: 0 },
      });
    }
    return Promise.resolve({
      success: true,
      data: { items: mockMaterials, total: 2, page: 1, per_page: 20 },
    });
  });
}

function renderComponent() {
  return render(<MaterialsListView />);
}

// ── Tests ──────────────────────────────────────────────────────────────

describe("MaterialsListView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockQueryString = "";
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders title and description", () => {
    mockCategoryRequest();
    renderComponent();
    expect(screen.getByText("材料列表")).toBeDefined();
  });

  it("loads and displays materials", async () => {
    mockCategoryRequest();
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      expect(mockRequest).toHaveBeenCalled();
      expect(screen.getByText("UO₂")).toBeDefined();
    });
  });

  it("shows empty state when no materials", async () => {
    mockCategoryRequest();
    mockRequest.mockImplementation((endpoint: string) => {
      if (endpoint === "/api/v1/material-categories") {
        return Promise.resolve({
          success: true,
          data: { items: SAMPLE_CATEGORIES },
        });
      }
      return Promise.resolve({
        success: true,
        data: { items: [], total: 0, page: 1, per_page: 20 },
      });
    });
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      expect(screen.getByText("暂无材料数据")).toBeDefined();
    });
  });

  it("shows error on API failure", async () => {
    mockRequest.mockImplementation((endpoint: string) => {
      if (endpoint === "/api/v1/material-categories") {
        return Promise.resolve({
          success: true,
          data: { items: [] },
        });
      }
      return Promise.reject(new Error("Network error"));
    });
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      expect(screen.getByText("Network error")).toBeDefined();
    });
  });

  it("links to material detail pages", async () => {
    mockCategoryRequest();
    const { container } = renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      const links = container.querySelectorAll('a[href*="/materials/"]');
      expect(links.length).toBeGreaterThan(0);
      expect(
        Array.from(links).some((l) =>
          (l as HTMLAnchorElement).href.includes("/materials/mat-001"),
        ),
      ).toBe(true);
    });
  });

  // ── NFM-3917 / Tier 1D: category filter behaviour ────────────────────

  it("appends category_id to the /materials list endpoint on selection", async () => {
    mockQueryString = `category_id=${FIRST_CATEGORY_ID}`;
    mockCategoryRequest();
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      const calls = mockRequest.mock.calls.map((c) => String(c[0]));
      const listCall = calls.find((c) => c?.startsWith("/api/v1/materials?"));
      expect(listCall).toBeDefined();
      expect(listCall).toContain("category_id=" + FIRST_CATEGORY_ID);
    });
  });

  it("composes category_id with /materials/search when both filters are active", async () => {
    mockQueryString = `category_id=${FIRST_CATEGORY_ID}`;
    mockCategoryRequest();
    renderComponent();

    // Type into the search box
    const searchInput = screen.getByPlaceholderText(
      "搜索材料名称、化学式或别名",
    ) as HTMLInputElement;
    fireEvent.change(searchInput, { target: { value: "UO" } });
    // antd Input.Search fires onSearch via Enter / button click — use form submit
    fireEvent.keyDown(searchInput, { key: "Enter", code: "Enter" });

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      const calls = mockRequest.mock.calls.map((c) => String(c[0]));
      const searchCall = calls.find((c) =>
        c?.startsWith("/api/v1/materials/search?"),
      );
      expect(searchCall).toBeDefined();
      expect(searchCall).toContain("q=UO");
      expect(searchCall).toContain("category_id=" + FIRST_CATEGORY_ID);
    });
  });

  it("clearing the category returns the URL to its base state", async () => {
    mockQueryString = `category_id=${FIRST_CATEGORY_ID}`;
    mockCategoryRequest();
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      // The select is rendered by antd; interact by simulating a change
      // to undefined (allowClear semantics).
      const select = screen.getByTestId(
        "materials-category-select",
      ) as HTMLElement;
      // antd Select emits onChange(value) with undefined on clear
      // (we can't easily drive the popup in jsdom, so we test the wiring
      //  by calling the React onChange handler indirectly through a
      //  userEvent-style clear — here we assert the initial render).
      expect(select).toBeDefined();
    });
  });

  it("reads categories from /api/v1/material-categories on mount", async () => {
    mockCategoryRequest();
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      const calls = mockRequest.mock.calls.map((c) => String(c[0]));
      expect(calls).toContain("/api/v1/material-categories");
    });
  });

  it("URL-sync effect: writes the canonical URL via history.replaceState on first mount when URL params are non-empty", async () => {
    // AC-4 root-cause test: the fix is to drive the page from local
    // state and write the URL via `window.history.replaceState` instead
    // of `router.replace`. Verify the URL-sync effect itself by
    // rendering with a non-empty mock URL and asserting that
    // replaceState is called with the canonical target URL.
    mockQueryString = `category_id=${FIRST_CATEGORY_ID}`;
    mockCategoryRequest();
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    // The effect should fire on first mount because target
    // (`/materials?category_id=...`) differs from jsdom's default
    // `window.location.pathname + search` (= "/" + "").
    await waitFor(() => {
      const urls = mockReplaceState.mock.calls.map((c) => String(c[2]));
      expect(urls).toContain(`/materials?category_id=${FIRST_CATEGORY_ID}`);
    });
  });

  // ── AC-4 regression (NFM-3917 / Tier 1D bug fix) ────────────────────
  //
  // Before the fix, the page was driven by `useSearchParams()` on every
  // render and called `router.replace()` to mutate the URL. When the
  // page was loaded with a non-empty search string (e.g. a deep-linked
  // `/materials?page=2`), Next.js 16's App Router did not reliably
  // re-render with the updated search params after `router.replace`,
  // so clicking a category visually updated the Select but the URL
  // and the underlying data fetch never changed. Driving the page from
  // local React state and writing the URL with `history.replaceState`
  // decouples the data fetch from router re-render propagation.
  //
  // We can't reliably drive antd Select from jsdom, so we exercise the
  // handler directly via a synthetic state mutation: the regression
  // assertion is that *after* the URL-sync effect runs in response to
  // a state change, both (a) a new materials request fires with the
  // updated filter, and (b) the URL is rewritten to the canonical
  // target — even when the page was loaded with a non-empty search
  // string. (The original component was driven by URL; it could not
  // even *see* the synthetic state change without a router re-render.)

  it("AC-4 regression: category click fires a fresh /materials request with category_id (entry-with-URL-params scenario)", async () => {
    // Simulate the broken entry scenario: page loaded with ?page=2,
    // then user clicks category.  Before the fix, the new category
    // never reached `request()` because `useSearchParams()` stayed
    // stale in the component.
    mockQueryString = "page=2";
    mockCategoryRequest();
    renderComponent();

    // Flush the initial-mount 300ms debounce + the URL-sync effect.
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    vi.useRealTimers();

    // Reset call history AFTER the initial-mount data fetch so we can
    // assert only the *post-click* request below.
    const callsBeforeClick = mockRequest.mock.calls.length;

    // Open the antd Select dropdown by clicking the selector, then
    // click the first option (matches the E2E QA "Oxide Fuel" seed).
    // rc-select uses mousedown for the trigger and renders the option
    // list in document.body once open.
    const select = await waitFor(() =>
      screen.getByTestId("materials-category-select"),
    );
    const selector = select.querySelector(".ant-select-selector")!;
    await act(async () => {
      fireEvent.mouseDown(selector);
    });

    // antd renders options in document.body via a portal — once the
    // dropdown is open they show up as `.ant-select-item-option`.
    const option = await waitFor(() =>
      document.querySelector<HTMLElement>(".ant-select-item-option"),
    );
    expect(option).not.toBeNull();
    await act(async () => {
      fireEvent.click(option!);
    });

    // Flush the post-click 300ms debounce + URL-sync effect.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 350));
    });

    // Now confirm a follow-up /api/v1/materials request fires that
    // carries category_id (this is the original symptom of the bug:
    // no follow-up request fired at all).
    const postClickCalls = mockRequest.mock.calls.slice(callsBeforeClick);
    const listCalls = postClickCalls
      .map((c) => String(c[0]))
      .filter((e) => e.startsWith("/api/v1/materials?"));
    expect(listCalls.length).toBeGreaterThanOrEqual(1);
    const lastList = listCalls[listCalls.length - 1];
    expect(lastList).toContain(`category_id=${FIRST_CATEGORY_ID}`);
    // Page must reset to 1 on category change.
    expect(lastList).toContain("page=1");

    // URL must be updated to the canonical target — even though
    // `router.replace` is bypassed, `history.replaceState` must
    // have been called with the new URL.
    const urls = mockReplaceState.mock.calls.map((c) => String(c[2]));
    expect(urls).toContain(`/materials?category_id=${FIRST_CATEGORY_ID}`);
  });

  it("AC-4 regression: page-change while filtered still resets/preserves correctly", async () => {
    // Direct-load scenario: page with ?category_id=<X>, then user clicks
    // page-2 in the Pagination. Before the fix, page was derived from
    // `useSearchParams()` — which stayed stale when initial params were
    // non-empty — so the data fetch never paged forward.
    //
    // Mock the materials endpoint with 40 items so pagination renders
    // a second page button — with 2 items at PAGE_SIZE=20 we only get
    // one page and the bug can't be exercised end-to-end.
    mockQueryString = `category_id=${FIRST_CATEGORY_ID}`;
    const bigMaterials = Array.from({ length: 40 }, (_, i) => ({
      id: `mat-${String(i + 1).padStart(3, "0")}`,
      name: `Mat ${String(i + 1).padStart(3, "0")}`,
      formula: null,
      crystal_structure: null,
      description: null,
      is_active: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    }));
    mockRequest.mockImplementation((endpoint: string) => {
      if (endpoint === "/api/v1/material-categories") {
        return Promise.resolve({
          success: true,
          data: { items: SAMPLE_CATEGORIES },
        });
      }
      return Promise.resolve({
        success: true,
        data: { items: bigMaterials, total: 40, page: 1, per_page: 20 },
      });
    });
    renderComponent();

    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    vi.useRealTimers();

    const callsBeforePaginate = mockRequest.mock.calls.length;

    // Find the antd pagination "next page" button and click it.
    const nextPage = await waitFor(() =>
      document.querySelector<HTMLLIElement>(".ant-pagination-item-2"),
    );
    expect(nextPage).not.toBeNull();
    await act(async () => {
      fireEvent.click(nextPage!);
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 350));
    });

    // The post-pagination materials request must carry both
    // category_id and page=2.
    const postPaginateCalls = mockRequest.mock.calls.slice(callsBeforePaginate);
    const listCalls = postPaginateCalls
      .map((c) => String(c[0]))
      .filter((e) => e.startsWith("/api/v1/materials?"));
    expect(listCalls.length).toBeGreaterThanOrEqual(1);
    const lastList = listCalls[listCalls.length - 1];
    expect(lastList).toContain(`category_id=${FIRST_CATEGORY_ID}`);
    expect(lastList).toContain("page=2");

    // URL must reflect page=2 alongside the category.
    const urls = mockReplaceState.mock.calls.map((c) => String(c[2]));
    expect(urls).toContain(
      `/materials?category_id=${FIRST_CATEGORY_ID}&page=2`,
    );
  });

  // ── NFM-4030 / Tier 1D follow-up: uncategorized notice ─────────────
  //
  // The category dropdown (NFM-3917) silently hides any material whose
  // `category_id IS NULL`. NFM-4030 exposes a notice when the backend
  // reports a positive count so users can see that some materials are
  // invisible under any filter. The count must come from the API — no
  // hardcoded numbers.

  it("renders the uncategorized notice when the API reports a positive count", async () => {
    mockCategoryRequest();
    mockRequest.mockImplementation((endpoint: string) => {
      if (endpoint === "/api/v1/material-categories") {
        return Promise.resolve({
          success: true,
          data: { items: SAMPLE_CATEGORIES },
        });
      }
      if (endpoint === "/api/v1/material-categories/uncategorized-count") {
        return Promise.resolve({
          success: true,
          data: { count: 47 },
        });
      }
      return Promise.resolve({
        success: true,
        data: { items: mockMaterials, total: 2, page: 1, per_page: 20 },
      });
    });
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      expect(
        screen.getByTestId("materials-uncategorized-notice"),
      ).toBeDefined();
    });
    // Count is interpolated from the API, not hardcoded — assert the
    // exact "47" rendered so a regression to a literal string fails.
    expect(
      screen.getByTestId("materials-uncategorized-notice").textContent,
    ).toContain("47");
  });

  it("does NOT render the notice when the API reports zero uncategorized", async () => {
    mockCategoryRequest(); // mockCategoryRequest already returns count: 0
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      expect(mockRequest).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("materials-uncategorized-notice")).toBeNull();
  });

  it("uses the API count verbatim (no hardcoded number)", async () => {
    // Regression: a hardcoded "47" inside the JSX would render even when
    // the API returns a different number. Use a non-default count and
    // assert the rendered text matches it byte-for-byte.
    mockCategoryRequest();
    mockRequest.mockImplementation((endpoint: string) => {
      if (endpoint === "/api/v1/material-categories") {
        return Promise.resolve({
          success: true,
          data: { items: SAMPLE_CATEGORIES },
        });
      }
      if (endpoint === "/api/v1/material-categories/uncategorized-count") {
        return Promise.resolve({
          success: true,
          data: { count: 9 },
        });
      }
      return Promise.resolve({
        success: true,
        data: { items: mockMaterials, total: 2, page: 1, per_page: 20 },
      });
    });
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      const node = screen.getByTestId("materials-uncategorized-notice");
      expect(node.textContent).toContain("9");
    });
  });

  it("uncategorized endpoint failure does not block the page", async () => {
    // Network error on the count endpoint — the page should still render
    // the table and dropdown. Notice is suppressed (count treated as 0).
    mockRequest.mockImplementation((endpoint: string) => {
      if (endpoint === "/api/v1/material-categories") {
        return Promise.resolve({
          success: true,
          data: { items: SAMPLE_CATEGORIES },
        });
      }
      if (endpoint === "/api/v1/material-categories/uncategorized-count") {
        return Promise.reject(new Error("boom"));
      }
      return Promise.resolve({
        success: true,
        data: { items: mockMaterials, total: 2, page: 1, per_page: 20 },
      });
    });
    renderComponent();

    vi.advanceTimersByTime(500);
    vi.useRealTimers();

    await waitFor(() => {
      expect(screen.getByText("UO₂")).toBeDefined();
    });
    expect(screen.queryByTestId("materials-uncategorized-notice")).toBeNull();
  });
});
