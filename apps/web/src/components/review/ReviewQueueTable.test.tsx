import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ReviewQueueTable } from './ReviewQueueTable'
import type { ReviewItem } from './ReviewQueueTable'

// ── Fixtures ────────────────────────────────────────────────────────────

const ITEM_1: ReviewItem = {
  id: 'item-1',
  title: 'UO2 密度数据',
  type: '物理性质',
  source: '论文 A (2024)',
  confidence: 0.92,
  status: 'pending',
  createdAt: '2024-06-15T10:30:00Z',
}

const ITEM_2: ReviewItem = {
  id: 'item-2',
  title: 'UO2 热导率',
  type: '热力学',
  source: '论文 B (2023)',
  confidence: 0.65,
  status: 'pending',
  createdAt: '2024-06-14T08:00:00Z',
}

const ITEM_3: ReviewItem = {
  id: 'item-3',
  title: 'UO2 熔点',
  type: '相图',
  source: '手册 C',
  confidence: 0.45,
  status: 'approved',
  createdAt: '2024-06-13T12:00:00Z',
}

const ALL_ITEMS: ReadonlyArray<ReviewItem> = [ITEM_1, ITEM_2, ITEM_3]

function defaultProps(overrides?: Partial<ConstructorParameters<typeof ReviewQueueTable>[0]>) {
  return {
    items: ALL_ITEMS,
    selectedIds: new Set<string>(),
    onSelect: vi.fn(),
    onSelectAll: vi.fn(),
    onBatchAction: vi.fn(),
    onItemAction: vi.fn(),
    ...overrides,
  }
}

// ── Tests ──────────────────────────────────────────────────────────────

describe('ReviewQueueTable', () => {
  // --- Rendering ---

  it('renders a semantic table with thead and tbody', () => {
    render(<ReviewQueueTable {...defaultProps()} />)
    expect(screen.getByRole('table')).toBeDefined()
    expect(screen.getAllByRole('row').length).toBeGreaterThan(1)
  })

  it('renders column headers', () => {
    render(<ReviewQueueTable {...defaultProps()} />)
    expect(screen.getByText('标题')).toBeDefined()
    expect(screen.getByText('类型')).toBeDefined()
    expect(screen.getByText('来源')).toBeDefined()
    expect(screen.getByText('置信度')).toBeDefined()
    expect(screen.getByText('状态')).toBeDefined()
    expect(screen.getByText('创建时间')).toBeDefined()
    expect(screen.getByText('操作')).toBeDefined()
  })

  it('renders item rows with correct data', () => {
    render(<ReviewQueueTable {...defaultProps()} />)
    expect(screen.getByText('UO2 密度数据')).toBeDefined()
    expect(screen.getByText('物理性质')).toBeDefined()
    expect(screen.getByText('论文 A (2024)')).toBeDefined()
  })

  it('renders status labels', () => {
    render(<ReviewQueueTable {...defaultProps()} />)
    expect(screen.getByText('待审')).toBeDefined()
    expect(screen.getByText('已通过')).toBeDefined()
  })

  // --- ConfidenceBadge integration ---

  it('renders ConfidenceBadge for each row', () => {
    render(<ReviewQueueTable {...defaultProps()} />)
    const badges = screen.getAllByRole('status')
    expect(badges.length).toBeGreaterThanOrEqual(3)
  })

  // --- Checkbox selection ---

  it('renders a select-all checkbox in the header', () => {
    render(<ReviewQueueTable {...defaultProps()} />)
    expect(screen.getByRole('checkbox', { name: '选择全部' })).toBeDefined()
  })

  it('renders individual row checkboxes with correct aria-labels', () => {
    render(<ReviewQueueTable {...defaultProps()} />)
    expect(screen.getByRole('checkbox', { name: '选择 UO2 密度数据' })).toBeDefined()
    expect(screen.getByRole('checkbox', { name: '选择 UO2 热导率' })).toBeDefined()
  })

  it('highlights selected rows', () => {
    const selectedIds = new Set(['item-1'])
    render(<ReviewQueueTable {...defaultProps({ selectedIds })} />)
    const bodyRows = screen.getAllByRole('row').slice(1)
    const firstDataRow = bodyRows[0]
    expect(firstDataRow?.className).toContain('bg-blue-900/20')
  })

  it('calls onSelectAll when select-all checkbox is toggled', () => {
    const onSelectAll = vi.fn()
    render(<ReviewQueueTable {...defaultProps({ onSelectAll })} />)
    fireEvent.click(screen.getByRole('checkbox', { name: '选择全部' }))
    expect(onSelectAll).toHaveBeenCalledWith(false)
  })

  it('calls onSelect when individual checkbox is toggled', () => {
    const onSelect = vi.fn()
    render(<ReviewQueueTable {...defaultProps({ onSelect })} />)
    fireEvent.click(screen.getByRole('checkbox', { name: '选择 UO2 密度数据' }))
    expect(onSelect).toHaveBeenCalledWith('item-1', true)
  })

  // --- Batch action bar ---

  it('does not show batch bar when nothing is selected', () => {
    render(<ReviewQueueTable {...defaultProps()} />)
    expect(screen.queryByText('已选择')).toBeNull()
  })

  it('shows batch bar when items are selected', () => {
    const selectedIds = new Set(['item-1', 'item-2'])
    render(<ReviewQueueTable {...defaultProps({ selectedIds })} />)
    expect(screen.getByText('已选择')).toBeDefined()
    expect(screen.getByText('2')).toBeDefined()
  })

  it('batch bar has correct aria-labels', () => {
    const selectedIds = new Set(['item-1'])
    render(<ReviewQueueTable {...defaultProps({ selectedIds })} />)
    expect(screen.getByLabelText('批量通过 1 项')).toBeDefined()
    expect(screen.getByLabelText('批量拒绝 1 项')).toBeDefined()
  })

  // --- Batch confirmation modal ---

  it('opens confirmation modal on batch approve click', () => {
    const selectedIds = new Set(['item-1'])
    render(<ReviewQueueTable {...defaultProps({ selectedIds })} />)
    fireEvent.click(screen.getByLabelText('批量通过 1 项'))
    expect(screen.getByText('批量通过')).toBeDefined()
    expect(screen.getByText(/确定通过选中的 1 项吗/)).toBeDefined()
  })

  it('opens confirmation modal on batch reject click', () => {
    const selectedIds = new Set(['item-1'])
    render(<ReviewQueueTable {...defaultProps({ selectedIds })} />)
    fireEvent.click(screen.getByLabelText('批量拒绝 1 项'))
    expect(screen.getByText('批量拒绝')).toBeDefined()
    expect(screen.getByText(/确定拒绝选中的 1 项吗/)).toBeDefined()
  })

  it('calls onBatchAction on confirm and closes modal', () => {
    const onBatchAction = vi.fn()
    const selectedIds = new Set(['item-1', 'item-2'])
    render(<ReviewQueueTable {...defaultProps({ selectedIds, onBatchAction })} />)

    fireEvent.click(screen.getByLabelText('批量通过 2 项'))
    fireEvent.click(screen.getByText('确认通过'))

    expect(onBatchAction).toHaveBeenCalledWith('approve', ['item-1', 'item-2'])
    expect(screen.queryByText('批量通过')).toBeNull()
  })

  it('closes modal on cancel', () => {
    const selectedIds = new Set(['item-1'])
    render(<ReviewQueueTable {...defaultProps({ selectedIds })} />)

    fireEvent.click(screen.getByLabelText('批量通过 1 项'))
    fireEvent.click(screen.getByText('取消'))

    expect(screen.queryByText('批量通过')).toBeNull()
  })

  // --- Individual row actions ---

  it('calls onItemAction for approve button', () => {
    const onItemAction = vi.fn()
    render(<ReviewQueueTable {...defaultProps({ onItemAction })} />)

    const approveButtons = screen.getAllByLabelText('通过 UO2 密度数据')
    fireEvent.click(approveButtons[0])
    expect(onItemAction).toHaveBeenCalledWith('item-1', 'approve')
  })

  it('calls onItemAction for reject button', () => {
    const onItemAction = vi.fn()
    render(<ReviewQueueTable {...defaultProps({ onItemAction })} />)

    const rejectButtons = screen.getAllByLabelText('拒绝 UO2 密度数据')
    fireEvent.click(rejectButtons[0])
    expect(onItemAction).toHaveBeenCalledWith('item-1', 'reject')
  })

  it('disables action buttons for non-pending items', () => {
    render(<ReviewQueueTable {...defaultProps()} />)

    const approveButtons = screen.getAllByLabelText('通过 UO2 熔点')
    expect(approveButtons[0]).toBeDisabled()
    const rejectButtons = screen.getAllByLabelText('拒绝 UO2 熔点')
    expect(rejectButtons[0]).toBeDisabled()
  })

  // --- Pagination ---

  it('renders pagination when provided', () => {
    const pagination = { page: 1, total: 25, pageSize: 10, onChange: vi.fn() }
    render(<ReviewQueueTable {...defaultProps({ pagination })} />)
    expect(screen.getByText('共 25 条')).toBeDefined()
  })

  it('calls onChange when next page is clicked', () => {
    const pagination = { page: 1, total: 25, pageSize: 10, onChange: vi.fn() }
    render(<ReviewQueueTable {...defaultProps({ pagination })} />)
    fireEvent.click(screen.getByLabelText('下一页'))
    expect(pagination.onChange).toHaveBeenCalledWith(2)
  })

  it('calls onChange when previous page is clicked', () => {
    const pagination = { page: 2, total: 25, pageSize: 10, onChange: vi.fn() }
    render(<ReviewQueueTable {...defaultProps({ pagination })} />)
    fireEvent.click(screen.getByLabelText('上一页'))
    expect(pagination.onChange).toHaveBeenCalledWith(1)
  })

  it('disables previous on first page', () => {
    const pagination = { page: 1, total: 25, pageSize: 10, onChange: vi.fn() }
    render(<ReviewQueueTable {...defaultProps({ pagination })} />)
    expect(screen.getByLabelText('上一页')).toBeDisabled()
  })

  // --- Loading state ---

  it('shows loading overlay when loading is true', () => {
    render(<ReviewQueueTable {...defaultProps({ loading: true })} />)
    expect(screen.getByLabelText('加载中')).toBeDefined()
  })

  // --- Empty state ---

  it('shows empty state when no items', () => {
    render(<ReviewQueueTable {...defaultProps({ items: [], loading: false })} />)
    expect(screen.getByText('暂无待审项目')).toBeDefined()
  })

  it('does not show empty state when loading', () => {
    render(<ReviewQueueTable {...defaultProps({ items: [], loading: true })} />)
    expect(screen.queryByText('暂无待审项目')).toBeNull()
  })
})
