import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SemanticSearchResults } from './SemanticSearchResults'
import type { RagCitation } from '@/lib/rag-api'

function createCitation(overrides: Partial<RagCitation> = {}): RagCitation {
  return {
    id: 'cit-1',
    source: '材料数据库',
    excerpt: '铀-235密度为19.1 g/cm³',
    confidence: 0.95,
    ...overrides,
  }
}

describe('SemanticSearchResults', () => {
  it('shows empty prompt when no answer and not loading', () => {
    render(
      <SemanticSearchResults
        answer=""
        citations={[]}
        loading={false}
        error={null}
      />,
    )

    expect(screen.getByText('请输入查询内容进行语义检索')).toBeInTheDocument()
  })

  it('shows loading spinner when loading', () => {
    const { container } = render(
      <SemanticSearchResults
        answer=""
        citations={[]}
        loading={true}
        error={null}
      />,
    )

    // Antd Spin renders aria-busy="true" (tip text not rendered in jsdom)
    const spinner = container.querySelector('[aria-busy="true"]')
    expect(spinner).toBeInTheDocument()
  })

  it('shows error message when error is present', () => {
    render(
      <SemanticSearchResults
        answer=""
        citations={[]}
        loading={false}
        error="网络错误"
      />,
    )

    expect(screen.getByText('检索失败：网络错误')).toBeInTheDocument()
  })

  it('renders AI answer when answer is present', () => {
    render(
      <SemanticSearchResults
        answer="铀-235的密度约为19.1 g/cm³"
        citations={[]}
        loading={false}
        error={null}
      />,
    )

    expect(screen.getByText('铀-235的密度约为19.1 g/cm³')).toBeInTheDocument()
    expect(screen.getByText('AI 回答')).toBeInTheDocument()
  })

  it('renders citation cards with source and excerpt', () => {
    const citation = createCitation()
    render(
      <SemanticSearchResults
        answer="铀-235的密度约为19.1 g/cm³"
        citations={[citation]}
        loading={false}
        error={null}
      />,
    )

    expect(screen.getByText('材料数据库')).toBeInTheDocument()
    expect(screen.getByText('铀-235密度为19.1 g/cm³')).toBeInTheDocument()
    expect(screen.getByText('相关片段 (1)')).toBeInTheDocument()
  })

  it('renders citation count header', () => {
    const citations = [createCitation(), createCitation({ id: 'cit-2' })]
    render(
      <SemanticSearchResults
        answer="回答"
        citations={citations}
        loading={false}
        error={null}
      />,
    )

    expect(screen.getByText('相关片段 (2)')).toBeInTheDocument()
  })

  it('renders citation with data-testid for each chunk', () => {
    const citation = createCitation({ id: 'chunk-42' })
    render(
      <SemanticSearchResults
        answer="回答"
        citations={[citation]}
        loading={false}
        error={null}
      />,
    )

    expect(screen.getByTestId('rag-chunk-chunk-42')).toBeInTheDocument()
  })

  it('renders source label when citation has url', () => {
    const citation = createCitation({ url: 'https://example.com/doc' })
    render(
      <SemanticSearchResults
        answer="回答"
        citations={[citation]}
        loading={false}
        error={null}
      />,
    )

    // Card is wrapped in <a> for the URL; inner "查看来源" is a <span>
    expect(screen.getByText('查看来源')).toBeInTheDocument()
  })

  it('prefers error state over empty answer', () => {
    render(
      <SemanticSearchResults
        answer=""
        citations={[]}
        loading={false}
        error="超时"
      />,
    )

    expect(screen.getByText('检索失败：超时')).toBeInTheDocument()
  })
})
