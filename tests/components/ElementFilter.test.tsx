import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ElementFilter from '@/components/ElementFilter'

const ALL_ELEMENTS = ['U', 'Zr', 'Mo', 'Nb', 'O', 'Fe', 'He', 'Pu', 'Si', 'C', 'Cr', 'Ni', 'Ti', 'Sn', 'Hf']

describe('ElementFilter', () => {
  it('renders all elements as buttons', () => {
    render(
      <ElementFilter
        allElements={ALL_ELEMENTS}
        selected={[]}
        onToggle={() => {}}
      />
    )
    expect(screen.getByText('U')).toBeTruthy()
    expect(screen.getByText('Pu')).toBeTruthy()
    expect(screen.getByText('C')).toBeTruthy()
    expect(screen.getByText('Hf')).toBeTruthy()
  })

  it('highlights selected elements with active class', () => {
    render(
      <ElementFilter
        allElements={ALL_ELEMENTS}
        selected={['U', 'Zr']}
        onToggle={() => {}}
      />
    )
    const uBtn = screen.getByText('U')
    const zrBtn = screen.getByText('Zr')
    const moBtn = screen.getByText('Mo')
    expect(uBtn.className).toContain('bg-blue-600')
    expect(zrBtn.className).toContain('bg-blue-600')
    expect(moBtn.className).not.toContain('bg-blue-600')
  })

  it('filters elements by search input', () => {
    render(
      <ElementFilter
        allElements={ALL_ELEMENTS}
        selected={[]}
        onToggle={() => {}}
      />
    )
    const input = screen.getByPlaceholderText('搜索元素...')
    fireEvent.change(input, { target: { value: 'zr' } })
    expect(screen.getByText('Zr')).toBeTruthy()
    expect(screen.queryByText('U')).toBeNull()
    expect(screen.queryByText('Mo')).toBeNull()
  })

  it('calls onToggle when clicking an element button', () => {
    const onToggle = vi.fn()
    render(
      <ElementFilter
        allElements={ALL_ELEMENTS}
        selected={[]}
        onToggle={onToggle}
      />
    )
    fireEvent.click(screen.getByText('U'))
    expect(onToggle).toHaveBeenCalledWith('U')
  })

  it('shows no-match message when search yields nothing', () => {
    render(
      <ElementFilter
        allElements={ALL_ELEMENTS}
        selected={[]}
        onToggle={() => {}}
      />
    )
    const input = screen.getByPlaceholderText('搜索元素...')
    fireEvent.change(input, { target: { value: 'xyz' } })
    expect(screen.getByText('无匹配元素')).toBeTruthy()
  })

  it('clearing search restores all elements', () => {
    render(
      <ElementFilter
        allElements={ALL_ELEMENTS}
        selected={[]}
        onToggle={() => {}}
      />
    )
    const input = screen.getByPlaceholderText('搜索元素...')
    fireEvent.change(input, { target: { value: 'zr' } })
    expect(screen.queryByText('U')).toBeNull()
    fireEvent.change(input, { target: { value: '' } })
    expect(screen.getByText('U')).toBeTruthy()
    expect(screen.getByText('Zr')).toBeTruthy()
  })
})
