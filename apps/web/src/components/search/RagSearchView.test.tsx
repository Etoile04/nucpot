import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { RagSearchView } from './RagSearchView'

// Mock ragApi module
vi.mock('@/lib/rag-api', () => ({
  ragApi: {
    query: vi.fn(),
  },
}))

// Import the mocked module after vi.mock
import { ragApi } from '@/lib/rag-api'
import type { RagQueryResponse } from '@/lib/rag-api'
const mockedQuery = vi.mocked(ragApi.query)

describe('RagSearchView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the search input and button', () => {
    render(<RagSearchView />)

    expect(screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')).toBeInTheDocument()
    expect(screen.getByText('语义检索')).toBeInTheDocument()
  })

  it('renders hint text', () => {
    render(<RagSearchView />)

    expect(
      screen.getByText('输入自然语言问题，AI 将从知识图谱中检索相关内容并生成回答'),
    ).toBeInTheDocument()
  })

  it('disables button when input is empty', () => {
    render(<RagSearchView />)

    const button = screen.getByText('语义检索')
    expect(button).toBeDisabled()
  })

  it('enables button when input has text', async () => {
    render(<RagSearchView />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀-235' } })
    })

    const button = screen.getByText('语义检索')
    expect(button).toBeEnabled()
  })

  it('calls ragApi.query with query, mode "hybrid", and topK on submit', async () => {
    mockedQuery.mockResolvedValueOnce({
      answer: '铀-235的密度约为19.1 g/cm³',
      citations: [],
      conversationId: 'conv-1',
    })

    render(<RagSearchView />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀-235的密度' } })
    })

    await act(async () => {
      fireEvent.click(screen.getByText('语义检索'))
    })

    expect(mockedQuery).toHaveBeenCalledWith({
      query: '铀-235的密度',
      mode: 'hybrid',
      topK: 5,
    })
  })

  it('shows loading state during query', async () => {
    let resolvePromise: (value: unknown) => void = () => {}
    mockedQuery.mockReturnValueOnce(
      new Promise((resolve: (value: unknown) => void) => {
        resolvePromise = resolve
      }) as Promise<RagQueryResponse>,
    )

    render(<RagSearchView />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')
    await act(async () => {
      fireEvent.change(input, { target: { value: '钚-239' } })
    })

    await act(async () => {
      fireEvent.click(screen.getByText('语义检索'))
    })

    // Button should show loading text
    expect(screen.getByText('检索中...')).toBeInTheDocument()

    // Resolve the promise
    await act(async () => {
      resolvePromise!({
        answer: '钚-239',
        citations: [],
        conversationId: 'conv-2',
      })
    })
  })

  it('disables input during loading', async () => {
    let resolvePromise: (value: unknown) => void = () => {}
    mockedQuery.mockReturnValueOnce(
      new Promise((resolve: (value: unknown) => void) => {
        resolvePromise = resolve
      }) as Promise<RagQueryResponse>,
    )

    render(<RagSearchView />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀' } })
    })

    await act(async () => {
      fireEvent.click(screen.getByText('语义检索'))
    })

    expect(input).toBeDisabled()

    await act(async () => {
      resolvePromise!({
        answer: '回答',
        citations: [],
        conversationId: 'conv-3',
      })
    })
  })

  it('renders answer and citations after successful query', async () => {
    mockedQuery.mockResolvedValueOnce({
      answer: '铀-235密度为19.1 g/cm³',
      citations: [
        {
          id: 'cit-1',
          source: '材料数据库',
          excerpt: '密度数据',
          confidence: 0.95,
        },
      ],
      conversationId: 'conv-4',
    })

    render(<RagSearchView />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀密度' } })
    })

    await act(async () => {
      fireEvent.click(screen.getByText('语义检索'))
    })

    expect(screen.getByText('铀-235密度为19.1 g/cm³')).toBeInTheDocument()
    expect(screen.getByText('材料数据库')).toBeInTheDocument()
  })

  it('shows error message when query fails', async () => {
    mockedQuery.mockRejectedValueOnce(new Error('网络错误'))

    render(<RagSearchView />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀' } })
    })

    await act(async () => {
      fireEvent.click(screen.getByText('语义检索'))
    })

    expect(screen.getByText('检索失败：网络错误')).toBeInTheDocument()
  })

  it('shows generic error for non-Error exceptions', async () => {
    mockedQuery.mockRejectedValueOnce('string error')

    render(<RagSearchView />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀' } })
    })

    await act(async () => {
      fireEvent.click(screen.getByText('语义检索'))
    })

    expect(screen.getByText('检索失败：语义检索失败，请重试')).toBeInTheDocument()
  })

  it('does not submit when query is empty', async () => {
    render(<RagSearchView />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...')
    await act(async () => {
      fireEvent.change(input, { target: { value: '   ' } })
    })

    await act(async () => {
      fireEvent.click(screen.getByText('语义检索'))
    })

    expect(mockedQuery).not.toHaveBeenCalled()
  })

  it('accepts initialQuery prop', () => {
    render(<RagSearchView initialQuery="钚-239" />)

    const input = screen.getByPlaceholderText('描述您想了解的核材料属性或关系...') as HTMLInputElement
    expect(input.value).toBe('钚-239')
  })
})
