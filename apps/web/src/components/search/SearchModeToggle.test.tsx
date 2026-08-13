import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SearchModeToggle } from './SearchModeToggle'

describe('SearchModeToggle', () => {
  it('renders both mode options', () => {
    render(<SearchModeToggle value="text" onChange={vi.fn()} />)

    expect(screen.getByText('文本检索')).toBeInTheDocument()
    expect(screen.getByText('语义 (RAG) 检索')).toBeInTheDocument()
  })

  it('calls onChange with "semantic" when semantic option is clicked', () => {
    const onChange = vi.fn()
    render(<SearchModeToggle value="text" onChange={onChange} />)

    fireEvent.click(screen.getByText('语义 (RAG) 检索'))

    expect(onChange).toHaveBeenCalledWith('semantic')
  })

  it('calls onChange with "text" when text option is clicked', () => {
    const onChange = vi.fn()
    render(<SearchModeToggle value="semantic" onChange={onChange} />)

    fireEvent.click(screen.getByText('文本检索'))

    expect(onChange).toHaveBeenCalledWith('text')
  })

  it('applies block and max-width class', () => {
    const { container } = render(
      <SearchModeToggle value="text" onChange={vi.fn()} />,
    )

    const segmented = container.querySelector('.ant-segmented')
    expect(segmented?.className).toContain('ant-segmented-block')
  })
})
