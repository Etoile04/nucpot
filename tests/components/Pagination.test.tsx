import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Pagination from '@/components/Pagination'

describe('Pagination', () => {
  it('renders page numbers with current page highlighted', () => {
    render(<Pagination currentPage={2} totalPages={5} onPageChange={() => {}} />)
    expect(screen.getByText('2')).toHaveClass('bg-blue-600')
    expect(screen.getByText('1')).toBeTruthy()
    expect(screen.getByText('5')).toBeTruthy()
  })

  it('disables previous button on first page', () => {
    render(<Pagination currentPage={1} totalPages={5} onPageChange={() => {}} />)
    const prevBtn = screen.getByLabelText('上一页')
    expect(prevBtn).toBeDisabled()
  })

  it('disables next button on last page', () => {
    render(<Pagination currentPage={5} totalPages={5} onPageChange={() => {}} />)
    const nextBtn = screen.getByLabelText('下一页')
    expect(nextBtn).toBeDisabled()
  })

  it('calls onPageChange when clicking a page number', () => {
    const onPageChange = vi.fn()
    render(<Pagination currentPage={1} totalPages={5} onPageChange={onPageChange} />)
    fireEvent.click(screen.getByText('3'))
    expect(onPageChange).toHaveBeenCalledWith(3)
  })

  it('shows ellipsis for large page counts', () => {
    render(<Pagination currentPage={1} totalPages={20} onPageChange={() => {}} />)
    expect(screen.getByText('...')).toBeTruthy()
  })
})
