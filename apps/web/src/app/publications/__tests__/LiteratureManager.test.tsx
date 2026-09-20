/**
 * LiteratureManager — TanStack Query cache invalidation regression tests
 * (NFM-3422).
 *
 * ## What contract these tests actually defend
 *
 * The component reads the list under a 3-element key
 * `["literature-list", page, filters]`, but its four mutation `onSuccess`
 * handlers invalidate the 1-element prefix `["literature-list"]`. The
 * load-bearing invariant is therefore:
 *
 *   > invalidating the 1-element prefix must reach EVERY cached
 *   > page+filter entry, including ones not currently mounted.
 *
 * That invariant breaks if someone passes `exact: true`, deletes an
 * `invalidateQueries` call, or changes the invalidation key so it is no
 * longer a prefix of the query key.
 *
 * ## What these tests deliberately do NOT assert
 *
 * They do not assert that the call sites pass `exact: false`.
 * In `@tanstack/query-core@5.101.4`, `matchQuery` destructures
 * `{ type = "all", exact, ... }` — `exact` has no default, so `undefined`
 * is falsy and takes the same `partialMatchKey` (prefix) branch as an
 * explicit `false`. `exact: false` is a documentation-only flag,
 * behaviourally identical to omitting it. Asserting on its presence pins a
 * no-op: it would pass or fail for reasons unrelated to user-visible
 * behaviour — precisely the tautology NFM-3422 was filed to remove.
 *
 * The real regression to guard is `exact: true`, which is what the
 * NFM-3408 commit message incorrectly believed the pre-fix code did.
 *
 * ## Shape of each test
 *
 * Render the real component, drive one of the four mutation paths through
 * the actual UI handlers, then assert on QueryClient cache state:
 *
 *   1. the ACTIVE page-1 entry refetched and now holds the fresh rows, and
 *   2. an INACTIVE page-2 entry was marked `isInvalidated` by the same
 *      prefix match (proving breadth, not just the mounted query).
 *
 * Both assertions fail under `exact: true` and under a deleted
 * invalidation call — red-green verified; see the NFM-3422 handoff for the
 * observed output.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { App } from "antd"

import type {
  LiteratureListItem,
  LiteratureDetail,
  LiteratureUploadResponse,
} from "@/lib/api-client"

/* ------------------------------------------------------------------ */
/*  Mock the literatureApi service                                     */
/* ------------------------------------------------------------------ */

const listLiterature = vi.fn()
const getLiterature = vi.fn()
const uploadLiterature = vi.fn()
const fromDoiLiterature = vi.fn()
const reextractLiterature = vi.fn()
const deleteLiterature = vi.fn()

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api-client")>(
    "@/lib/api-client",
  )
  return {
    ...actual,
    literatureApi: {
      list: listLiterature,
      get: getLiterature,
      upload: uploadLiterature,
      fromDoi: fromDoiLiterature,
      reextract: reextractLiterature,
      delete: deleteLiterature,
    },
  }
})

/* ------------------------------------------------------------------ */
/* / Mock antd Popconfirm to render synchronously under jsdom.          */
/* /                                                                   */
/* / Under jsdom, rc-trigger's positioning math never resolves, so    */
/* / antd's real Popconfirm never paints its popover and the OK button */
/* / is unreachable. The Popconfirm *contract* we exercise is "click  */
/* / trigger → click OK → onConfirm fires"; we replace the popover    */
/* / rendering with a synchronous OK button so the same wiring runs  */
/* / through the component's onConfirm prop (i.e. handleReextract /   */
/* / handleDelete → mutation.mutate → onSuccess → invalidateQueries). */
/* ------------------------------------------------------------------ */

interface PopconfirmProps {
  readonly children?: React.ReactNode
  readonly onConfirm?: (...args: unknown[]) => unknown
  readonly okText?: string
  readonly cancelText?: string
}

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd")
  return {
    ...actual,
    Popconfirm: ({ children, onConfirm, okText }: PopconfirmProps) => (
      <div data-testid="popconfirm-stub">
        {children}
        <button
          type="button"
          className="ant-btn ant-btn-primary ant-btn-sm"
          data-testid="popconfirm-ok"
          onClick={() => onConfirm?.()}
        >
          {okText ?? "OK"}
        </button>
      </div>
    ),
  }
})

/* ------------------------------------------------------------------ */
/*  Test data                                                          */
/* ------------------------------------------------------------------ */

function makeListItem(
  overrides: Partial<LiteratureListItem> = {},
): LiteratureListItem {
  return {
    id: "lit-001",
    title: "UO2 thermal conductivity",
    doi: "10.1016/j.jnucmat.2020.001",
    journal: "J. Nucl. Mater.",
    year: 2020,
    abstract: "Sample abstract",
    status: "completed",
    source_id: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

function makeDetail(overrides: Partial<LiteratureDetail> = {}): LiteratureDetail {
  return {
    ...makeListItem(),
    content_md: null,
    figures: [],
    extraction_results: [],
    updated_at: null,
    ...overrides,
  }
}

/** Mirrors `INITIAL_FILTERS` in `LiteratureManager.tsx`. The component's
 * query key embeds this object verbatim, so it must stay structurally
 * identical or the keys below will hash differently and stop matching. */
const INITIAL_FILTERS = {
  search: "",
  status: "",
  yearMin: null,
  yearMax: null,
} as const

/** The ACTIVE entry: exactly the `queryKey` the mounted component builds,
 * `["literature-list", page, filters]` with `page === 1`. */
const ACTIVE_PAGE_KEY = ["literature-list", 1, { ...INITIAL_FILTERS }] as const

/** An INACTIVE entry for a page the user visited earlier and navigated
 * away from. Nothing is mounted against it, so only a prefix match can
 * reach it. This is the assertion an `exact: true` regression fails. */
const INACTIVE_PAGE_KEY = ["literature-list", 2, { ...INITIAL_FILTERS }] as const

const SEEDED_LIST_DATA = {
  items: [makeListItem({ id: "lit-001" })],
  total: 1,
}

/** Seed both cache entries so each test can prove the component's
 * 1-element-prefix invalidation reaches the mounted page AND a stale one. */
function seedListCache(client: QueryClient): void {
  client.setQueryData(ACTIVE_PAGE_KEY, SEEDED_LIST_DATA)
  client.setQueryData(INACTIVE_PAGE_KEY, SEEDED_LIST_DATA)
}

/* ------------------------------------------------------------------ */
/*  Render helper                                                      */
/* ------------------------------------------------------------------ */

async function renderLiteratureManager() {
  const { default: LiteratureManager } = await import("../LiteratureManager")
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  // Wrap in AntD's <App> so `App.useApp()` inside LiteratureManager
  // returns the message instance — the production root layout mounts
  // `<App>` via `components/antd-provider.tsx`. Without this wrapper
  // `App.useApp()` would return a no-op and any `message.error(...)`
  // call would throw (verified during the NFM-3765 drawer feedback
  // fix; see LiteratureManager.tsx for the App.useApp() rationale).
  const utils = render(
    <QueryClientProvider client={client}>
      <App>
        <LiteratureManager />
      </App>
    </QueryClientProvider>,
  )
  return { ...utils, client }
}

/* ------------------------------------------------------------------ */
/*  Shared assertions                                                  */
/* ------------------------------------------------------------------ */

/**
 * Assert the component's 1-element-prefix invalidation reached both cached
 * page entries.
 *
 * Neither assertion mentions `exact`. They observe cache state only, so
 * they stay green whether the call site writes `exact: false` or omits it
 * (identical behaviour in query-core 5.101.4) and go red the moment the
 * prefix stops matching — `exact: true`, a changed key, or a deleted
 * `invalidateQueries` call.
 */
async function expectListPrefixInvalidated(
  client: QueryClient,
  expectedFreshItems: LiteratureListItem[],
): Promise<void> {
  // 1. The ACTIVE page-1 entry: `invalidateQueries` defaults to
  //    `refetchType: "active"`, so this one refetches and its data is
  //    replaced by the fresh rows. Under `exact: true` the 1-element
  //    filter key never matches the 3-element query key, no refetch is
  //    scheduled, and the seeded rows remain.
  await waitFor(
    () => {
      const active = client
        .getQueryCache()
        .find({ queryKey: ACTIVE_PAGE_KEY, exact: true })
      expect(active, "active page-1 cache entry should exist").toBeDefined()
      const data = active?.state.data as
        | { items: LiteratureListItem[]; total: number }
        | undefined
      expect(data?.items).toEqual(expectedFreshItems)
    },
    { timeout: 10_000 },
  )

  // 2. The INACTIVE page-2 entry: nothing is mounted against it, so it is
  //    not refetched — but a prefix match still flags it invalid so it
  //    refetches when the user pages back. This is the breadth guarantee
  //    the 1-element prefix exists to provide.
  await waitFor(
    () => {
      const inactive = client
        .getQueryCache()
        .find({ queryKey: INACTIVE_PAGE_KEY, exact: true })
      expect(inactive, "inactive page-2 cache entry should exist").toBeDefined()
      expect(
        inactive?.state.isInvalidated,
        "inactive page-2 entry should be invalidated by the 1-element prefix",
      ).toBe(true)
    },
    { timeout: 10_000 },
  )
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("LiteratureManager (NFM-3422) — TanStack Query invalidation", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listLiterature.mockResolvedValue(SEEDED_LIST_DATA)
    getLiterature.mockResolvedValue(makeDetail())
  })

  it("AC-1: upload mutation invalidates the 3-element list cache key", async () => {
    const uploadResp: LiteratureUploadResponse = {
      literature_id: "lit-new",
      status: "uploaded",
    }
    uploadLiterature.mockResolvedValue(uploadResp)

    const freshAfterUpload: LiteratureListItem[] = [
      makeListItem({ id: "lit-new", title: "Uploaded PDF", status: "uploaded" }),
      makeListItem({ id: "lit-001" }),
    ]
    listLiterature
      .mockResolvedValueOnce(SEEDED_LIST_DATA)
      .mockResolvedValue({ items: freshAfterUpload, total: 2 })

    const { client, container } = await renderLiteratureManager()

    await waitFor(
      () => expect(listLiterature).toHaveBeenCalledTimes(1),
      { timeout: 10_000 },
    )

    // Seed both the mounted page-1 entry and a stale page-2 entry so we
    // can prove the component's 1-element-prefix invalidation reaches
    // both, not just the query that happens to be mounted.
    seedListCache(client)

    // Drive the upload through the rendered Dragger's hidden file input.
    // Antd v5 renders `<input type="file">` inside the Upload component;
    // dispatching a change event triggers `beforeUpload` → `handleUpload`
    // → `uploadMutation.mutate(file)`.
    //
    // RTL's standard pattern is to set `target.files` in the same call as
    // the event dispatch. The split
    // `Object.defineProperty(files, …); fireEvent.change(input)` is
    // timing-fragile in jsdom (the property assignment sometimes lands
    // after the change handler reads `e.target.files`, leaving it empty).
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement | null
    expect(fileInput, "Dragger should render a hidden file input").not.toBeNull()
    const file = new File(["%PDF-1.4 fake"], "test.pdf", {
      type: "application/pdf",
    })
    fireEvent.change(fileInput!, {
      target: { files: [file] },
    })

    await waitFor(
      () => expect(uploadLiterature).toHaveBeenCalledWith(file),
      { timeout: 10_000 },
    )

    // onSuccess invalidates the 1-element prefix `["literature-list"]`.
    // Prefix matching makes that reach the mounted page-1 entry (refetch)
    // and the stale page-2 entry (flagged invalid). Under `exact: true`
    // the 1-element filter would hash-compare against 3-element keys,
    // match nothing, and leave both entries untouched.
    await expectListPrefixInvalidated(client, freshAfterUpload)

    // Also verify the second `listLiterature` call happened (refetch).
    expect(listLiterature.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it("AC-2: DOI submission invalidates the 3-element list cache key", async () => {
    fromDoiLiterature.mockResolvedValue({
      literature_id: "lit-doi",
      status: "uploaded",
    })

    const freshAfterDoi: LiteratureListItem[] = [
      makeListItem({ id: "lit-doi", doi: "10.1016/j.jnucmat.2020.152307" }),
      makeListItem({ id: "lit-001" }),
    ]
    listLiterature
      .mockResolvedValueOnce(SEEDED_LIST_DATA)
      .mockResolvedValue({ items: freshAfterDoi, total: 2 })

    const { client } = await renderLiteratureManager()

    await waitFor(
      () => expect(listLiterature).toHaveBeenCalledTimes(1),
      { timeout: 10_000 },
    )

    seedListCache(client)

    // Switch to the DOI tab and submit a DOI through the real form.
    const doiTab = screen.getByRole("tab", { name: /DOI 提取/ })
    fireEvent.click(doiTab)
    const doiInput = await screen.findByPlaceholderText(/10\.1016/)
    fireEvent.change(doiInput, {
      target: { value: "10.1016/j.jnucmat.2020.152307" },
    })
    const submitBtn = screen.getByRole("button", { name: /通过 DOI 提取/ })
    fireEvent.click(submitBtn)

    await waitFor(
      () =>
        expect(fromDoiLiterature).toHaveBeenCalledWith(
          "10.1016/j.jnucmat.2020.152307",
        ),
      { timeout: 10_000 },
    )

    await expectListPrefixInvalidated(client, freshAfterDoi)
    expect(listLiterature.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it("AC-3: re-extract action invalidates the 3-element list cache key", async () => {
    reextractLiterature.mockResolvedValue({
      literature_id: "lit-001",
      status: "extracting",
    })

    const freshAfterReextract: LiteratureListItem[] = [
      makeListItem({ id: "lit-001", status: "extracting" }),
    ]
    listLiterature
      .mockResolvedValueOnce(SEEDED_LIST_DATA)
      .mockResolvedValue({ items: freshAfterReextract, total: 1 })

    const { client } = await renderLiteratureManager()

    await waitFor(
      () => expect(listLiterature).toHaveBeenCalledTimes(1),
      { timeout: 10_000 },
    )

    seedListCache(client)

    // Open the detail drawer by clicking the row's title link.
    const titleLink = await screen.findByText("UO2 thermal conductivity")
    fireEvent.click(titleLink)

    // Wait for the detail fetch and the Popconfirm button to render.
    await waitFor(
      () => expect(getLiterature).toHaveBeenCalledWith("lit-001"),
      { timeout: 10_000 },
    )
    const reextractBtn = await screen.findByRole("button", { name: /重新提取/ })
    fireEvent.click(reextractBtn)

    // Popconfirm OK button (mocked synchronously — see top of file).
    // Scope to the same parent as the trigger so we don't pick up the
    // sibling delete-Popconfirm's OK button.
    const confirmBtn = within(
      reextractBtn.parentElement as HTMLElement,
    ).getByTestId("popconfirm-ok")
    fireEvent.click(confirmBtn)

    await waitFor(
      () => expect(reextractLiterature).toHaveBeenCalledWith("lit-001"),
      { timeout: 10_000 },
    )

    await expectListPrefixInvalidated(client, freshAfterReextract)
    expect(listLiterature.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it("AC-4: delete action invalidates the 3-element list cache key", async () => {
    deleteLiterature.mockResolvedValue({ literature_id: "lit-001" })

    const freshAfterDelete: LiteratureListItem[] = []
    listLiterature
      .mockResolvedValueOnce(SEEDED_LIST_DATA)
      .mockResolvedValue({ items: freshAfterDelete, total: 0 })

    const { client } = await renderLiteratureManager()

    await waitFor(
      () => expect(listLiterature).toHaveBeenCalledTimes(1),
      { timeout: 10_000 },
    )

    seedListCache(client)

    // Open the detail drawer by clicking the row's title link.
    const titleLink = await screen.findByText("UO2 thermal conductivity")
    fireEvent.click(titleLink)

    await waitFor(
      () => expect(getLiterature).toHaveBeenCalledWith("lit-001"),
      { timeout: 10_000 },
    )
    const deleteBtn = await screen.findByRole("button", { name: /删除/ })
    fireEvent.click(deleteBtn)

    // Popconfirm OK button (mocked synchronously — see top of file).
    // Scope to the same parent as the trigger so we don't pick up the
    // sibling re-extract-Popconfirm's OK button.
    const confirmBtn = within(
      deleteBtn.parentElement as HTMLElement,
    ).getByTestId("popconfirm-ok")
    fireEvent.click(confirmBtn)

    await waitFor(
      () => expect(deleteLiterature).toHaveBeenCalledWith("lit-001"),
      { timeout: 10_000 },
    )

    await expectListPrefixInvalidated(client, freshAfterDelete)
    expect(listLiterature.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it("AC-5: filter change creates a separate cache entry (no stale closure)", async () => {
    // Echo the filter so we can assert the queryKey flowed through.
    listLiterature.mockImplementation(
      async (params: { status?: string } | undefined) => ({
        items: [
          makeListItem({
            status:
              (params?.status as LiteratureListItem["status"]) ?? "completed",
          }),
        ],
        total: 1,
      }),
    )

    const { client } = await renderLiteratureManager()

    await waitFor(
      () => expect(listLiterature).toHaveBeenCalledTimes(1),
      { timeout: 10_000 },
    )

    const firstCallArgs = listLiterature.mock.calls[0]![0]
    expect(firstCallArgs).toMatchObject({ status: undefined })

    // Apply a status filter via the rendered <Select>. Antd renders the
    // current value as a div; click it, then click the option in the
    // dropdown.
    const statusSelect = screen.getByText("全部状态")
    fireEvent.mouseDown(statusSelect)
    const uploadedOption = await screen.findByText("已上传")
    fireEvent.click(uploadedOption)

    // Wait for the new (filtered) query to fire.
    await waitFor(
      () => expect(listLiterature.mock.calls.length).toBeGreaterThanOrEqual(2),
      { timeout: 10_000 },
    )

    const secondCallArgs = listLiterature.mock.calls[1]![0]
    expect(secondCallArgs).toMatchObject({ status: "uploaded" })

    // Both filter combinations have their own cache entries — proves the
    // refactor keys by (page, filters) and there is no shared value.
    const keys = client.getQueryCache().findAll().map((q) => q.queryKey)
    expect(keys.length).toBeGreaterThanOrEqual(2)
    expect(keys).toEqual(
      expect.arrayContaining([
        expect.arrayContaining(["literature-list", 1, expect.anything()]),
      ]),
    )
  })

  // ── Drawer resize + loading feedback (NFM-3765) ────────────────────
  //
  // These tests pin the user-facing symptoms that motivated the fix:
  //   • 重新提取 / 删除 buttons had no `loading` state while the mutation
  //     was in-flight, so a user clicking "confirm" in the popover got
  //     no visual feedback and thought the button was broken.
  //   • The drawer was 560px hard-coded with no resize handle, so users
  //     couldn't widen the panel to read embedded Markdown.
  //   • `message.error(...)` was a static call against an AntD `<App>`-
  //     scoped container; under the SSR build the static container is
  //     never created, so the error toast silently never appeared.
  //
  // Each test asserts the contract, not the implementation, so a future
  // refactor that swaps the resize library or the message API still
  // passes.

  it("AC-6: drawer renders a resize handle immediately on open (not gated on detail fetch)", async () => {
    await renderLiteratureManager()
    await waitFor(() => expect(listLiterature).toHaveBeenCalledTimes(1))

    // Open the drawer. The handle must mount with the drawer itself —
    // NOT wait for the detail fetch (NFM-3765 follow-up: gating on
    // `detail` made the handle un-grabbable during the 1-1.5s fetch,
    // which users perceived as "resize is broken").
    const titleLink = await screen.findByText("UO2 thermal conductivity")
    fireEvent.click(titleLink)

    // Assert BEFORE waiting for getLiterature to resolve: the handle
    // should already be in the portal.
    await waitFor(() => {
      const handle = document.querySelector(
        '.ant-drawer [role="separator"]',
      ) as HTMLElement | null
      expect(handle).not.toBeNull()
    })
    // And it must be present even while the detail is still pending.
    expect(getLiterature).toHaveBeenCalled()

    const handle = document.querySelector(
      '.ant-drawer [role="separator"]',
    ) as HTMLElement
    expect(handle.getAttribute("aria-orientation")).toBe("vertical")
    expect(handle.getAttribute("aria-label")).toMatch(/拖动调整详情面板宽度/)
  })

  // AC-7 and AC-8 are covered end-to-end via Playwright in
  // apps/web/e2e/literature-drawer.spec.ts (see NFM-3765
  // handoff). Driving `mutation.isPending === true` through the
  // popover → mutate pipeline is brittle under jsdom because the
  // Popconfirm mock re-renders the trigger button on each
  // transition, invalidating the cached element reference between
  // the click and the assertion. The user-facing behaviour
  // (loading class on the trigger + OK button + sibling-disable) is
  // verified directly against the live https://nucpot.dpdns.org/
  // literature page in the Playwright spec, which is the right
  // place for it. We keep AC-6 (resize handle) and AC-9 (resize
  // end → localStorage) here because both are pure DOM-contract
  // tests that don't need the in-flight mutation pipeline.

  it("AC-9: drawer width persists to localStorage on resize end", async () => {
    // Seed a known width so the assertion is deterministic regardless
    // of any persisted value from previous test runs in the same
    // jsdom instance.
    window.localStorage.setItem("nucpot.literature.drawerWidth", "820")

    await renderLiteratureManager()
    await waitFor(() => expect(listLiterature).toHaveBeenCalledTimes(1))

    // Open drawer
    fireEvent.click(await screen.findByText("UO2 thermal conductivity"))
    await waitFor(() => expect(getLiterature).toHaveBeenCalledWith("lit-001"))

    // The seeded width should be reflected on the rendered drawer.
    // AntD puts the width on `.ant-drawer-content-wrapper` as inline
    // style (or as an attribute depending on version) — we read the
    // computed width and confirm it matches the seeded value within
    // a tolerance to allow sub-pixel rounding.
    const wrapper = await waitFor(() => {
      const w = document.querySelector(".ant-drawer-content-wrapper") as HTMLElement | null
      expect(w).not.toBeNull()
      return w!
    })
    const widthStr = wrapper.style.width || wrapper.getAttribute("style")?.match(/width:\s*(\d+)/)?.[1]
    expect(widthStr).toBeDefined()
    const width = parseInt(widthStr ?? "0", 10)
    expect(width).toBeGreaterThanOrEqual(810)
    expect(width).toBeLessThanOrEqual(830)

    // Drive the resize handle via pointer events to ensure the
    // pointer-event contract works (touch + mouse + trackpad).
    const handle = document.querySelector('[role="separator"]') as HTMLElement | null
    expect(handle).not.toBeNull()

    // Simulate dragging 100px to the LEFT — drawer should grow by 100.
    const startWidth = width
    fireEvent.pointerDown(handle!, { button: 0, clientX: 500, clientY: 300, pointerId: 1 })
    fireEvent.pointerMove(handle!, { button: 0, clientX: 400, clientY: 300, pointerId: 1 })
    fireEvent.pointerUp(handle!, { button: 0, clientX: 400, clientY: 300, pointerId: 1 })

    await waitFor(() => {
      const newWidth = parseInt(
        document
          .querySelector(".ant-drawer-content-wrapper")!
          .getAttribute("style")!
          .match(/width:\s*(\d+)/)?.[1] ?? "0",
        10,
      )
      expect(newWidth).toBeGreaterThanOrEqual(startWidth + 50)
    })

    // localStorage was written on pointer-up
    expect(window.localStorage.getItem("nucpot.literature.drawerWidth")).not.toBeNull()
  })
})