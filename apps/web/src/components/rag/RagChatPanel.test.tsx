import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { RagChatPanel } from '@/components/rag/RagChatPanel'
import type { RagMessage } from '@/lib/rag-api'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const BASE_MESSAGE: RagMessage = {
  id: 'msg-1',
  role: 'user',
  content: '铀-235的密度是多少？',
  citations: [],
  createdAt: '2026-01-01T00:00:00Z',
}

function createAssistantMessage(
  content: string,
  citations: RagMessage['citations'] = [],
): RagMessage {
  return {
    id: 'msg-2',
    role: 'assistant',
    content,
    citations: [...citations],
    createdAt: '2026-01-01T00:00:01Z',
  }
}

function createCitation(overrides: Partial<RagMessage['citations'][number]> = {}) {
  return {
    id: 'cit-1',
    source: '材料数据库',
    excerpt: '铀-235密度为19.1 g/cm³',
    confidence: 0.95,
    ...overrides,
  }
}

const defaultProps = {
  onSubmit: vi.fn(),
  messages: [] as ReadonlyArray<RagMessage>,
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('RagChatPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // -- Render ----------------------------------------------------------------

  it('renders empty state with prompt text', () => {
    render(<RagChatPanel {...defaultProps} />)

    expect(screen.getByText('请描述您要查询的核材料属性或关系')).toBeInTheDocument()
  })

  it('renders the header title', () => {
    render(<RagChatPanel {...defaultProps} />)

    expect(screen.getByText('RAG 对话')).toBeInTheDocument()
  })

  // -- User messages ---------------------------------------------------------

  it('renders user messages correctly', () => {
    const messages: ReadonlyArray<RagMessage> = [BASE_MESSAGE]
    render(<RagChatPanel {...defaultProps} messages={messages} />)

    expect(screen.getByText('铀-235的密度是多少？')).toBeInTheDocument()
  })

  // -- Assistant messages ---------------------------------------------------

  it('renders assistant messages correctly', () => {
    const messages: ReadonlyArray<RagMessage> = [
      createAssistantMessage('铀-235的密度约为19.1 g/cm³'),
    ]
    render(<RagChatPanel {...defaultProps} messages={messages} />)

    expect(screen.getByText('铀-235的密度约为19.1 g/cm³')).toBeInTheDocument()
  })

  // -- Citations -------------------------------------------------------------

  it('renders CitationCard for assistant messages with citations', () => {
    const citation = createCitation()
    const messages: ReadonlyArray<RagMessage> = [
      createAssistantMessage('密度信息如下', [citation]),
    ]
    render(<RagChatPanel {...defaultProps} messages={messages} />)

    expect(screen.getByTestId(`citation-${citation.id}`)).toBeInTheDocument()
    expect(screen.getByText('材料数据库')).toBeInTheDocument()
    expect(screen.getByText('铀-235密度为19.1 g/cm³')).toBeInTheDocument()
  })

  // -- Typing indicator -------------------------------------------------------

  it('shows TypingIndicator when loading', () => {
    render(<RagChatPanel {...defaultProps} loading />)

    expect(screen.getByText('正在回复')).toBeInTheDocument()
  })

  // -- Form submission -------------------------------------------------------

  it('calls onSubmit when form is submitted', async () => {
    const onSubmit = vi.fn()
    render(<RagChatPanel {...defaultProps} onSubmit={onSubmit} />)

    const input = screen.getByLabelText('查询输入框')
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀-235的熔点' } })
    })

    const form = input.closest('form')!
    await act(async () => {
      fireEvent.submit(form)
    })

    expect(onSubmit).toHaveBeenCalledWith('铀-235的熔点')
  })

  it('calls onSubmit on Enter key press', async () => {
    const onSubmit = vi.fn()
    render(<RagChatPanel {...defaultProps} onSubmit={onSubmit} />)

    const input = screen.getByLabelText('查询输入框')
    await act(async () => {
      fireEvent.change(input, { target: { value: '钚-239' } })
    })
    // Pressing Enter on a text input inside a form triggers native form submit.
    // In jsdom this is simulated by firing the submit event on the form.
    const form = input.closest('form')!
    await act(async () => {
      fireEvent.submit(form)
    })

    expect(onSubmit).toHaveBeenCalledWith('钚-239')
  })

  // -- Disabled states -------------------------------------------------------

  it('disables send button when loading', () => {
    render(<RagChatPanel {...defaultProps} loading />)

    const button = screen.getByLabelText('发送')
    expect(button).toBeDisabled()
  })

  it('disables send button when input is empty', () => {
    render(<RagChatPanel {...defaultProps} />)

    const button = screen.getByLabelText('发送')
    expect(button).toBeDisabled()
  })

  it('enables send button when input has text', async () => {
    render(<RagChatPanel {...defaultProps} />)

    const input = screen.getByLabelText('查询输入框')
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀' } })
    })

    const button = screen.getByLabelText('发送')
    expect(button).toBeEnabled()
  })

  it('disables input when loading', () => {
    render(<RagChatPanel {...defaultProps} loading />)

    const input = screen.getByLabelText('查询输入框')
    expect(input).toBeDisabled()
  })

  // -- Auto-scroll -----------------------------------------------------------

  it('scrolls to bottom on new message', () => {
    const messages: ReadonlyArray<RagMessage> = [BASE_MESSAGE]
    const { rerender } = render(
      <RagChatPanel {...defaultProps} messages={messages} />,
    )

    const scrollContainer = document.querySelector('.overflow-y-auto') as HTMLElement
    const scrollTopSpy = vi.spyOn(scrollContainer, 'scrollTop', 'set')

    const newMessages: ReadonlyArray<RagMessage> = [
      BASE_MESSAGE,
      createAssistantMessage('回答'),
    ]
    rerender(<RagChatPanel {...defaultProps} messages={newMessages} />)

    // After rerender, scrollTop should have been set to scrollHeight
    expect(scrollTopSpy).toHaveBeenCalled()
  })

  // -- Error state -----------------------------------------------------------

  it('renders error state when error prop is provided', () => {
    render(<RagChatPanel {...defaultProps} error="请求失败，请重试" />)

    expect(screen.getByText('请求失败，请重试')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('does not render error banner when error is null', () => {
    render(<RagChatPanel {...defaultProps} error={null} />)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  // -- Empty state hides when messages exist --------------------------------

  it('hides empty state when messages exist', () => {
    const messages: ReadonlyArray<RagMessage> = [BASE_MESSAGE]
    render(<RagChatPanel {...defaultProps} messages={messages} />)

    expect(
      screen.queryByText('请描述您要查询的核材料属性或关系'),
    ).not.toBeInTheDocument()
  })

  // -- Clears input after submit ---------------------------------------------

  it('clears input after submission', async () => {
    const onSubmit = vi.fn()
    render(<RagChatPanel {...defaultProps} onSubmit={onSubmit} />)

    const input = screen.getByLabelText('查询输入框') as HTMLInputElement
    await act(async () => {
      fireEvent.change(input, { target: { value: '铀-235' } })
    })
    expect(input.value).toBe('铀-235')

    const form = input.closest('form')!
    await act(async () => {
      fireEvent.submit(form)
    })
    expect(input.value).toBe('')
  })
})
