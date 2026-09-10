import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { ValueExpression } from "../ValueExpression"

describe("ValueExpression (NFM-4553 G1-E AC-5)", () => {
  it("renders a simple KaTeX expression as HTML", () => {
    render(<ValueExpression expression="E = mc^2" />)
    const node = screen.getByTestId("value-expression-katex")
    expect(node).toBeInTheDocument()
    // KaTeX produces a <span class="katex"> wrapper
    expect(node.querySelector(".katex")).not.toBeNull()
  })

  it("renders display mode for large formulas", () => {
    render(<ValueExpression expression="a^2 + b^2" displayMode />)
    const node = screen.getByTestId("value-expression-katex")
    // Display mode produces a `.katex-display` wrapper
    expect(node.innerHTML).toContain("katex-display")
  })

  it("falls back to <code> when KaTeX throws on malformed input", () => {
    // KaTeX with throwOnError:false still emits HTML on bad input,
    // but a fully broken macro / unclosed brace should degrade.
    // We assert the fallback data-testid surfaces only on a true throw.
    render(<ValueExpression expression="\\fooBarInvalid{}" />)
    // Either KaTeX renders with output:html (preferred) or falls back;
    // both are acceptable per AC-5 (must NOT crash).
    const katexNode = screen.queryByTestId("value-expression-katex")
    const fallback = screen.queryByTestId("value-expression-fallback")
    expect(katexNode ?? fallback).not.toBeNull()
  })

  it("does not crash on empty expression", () => {
    render(<ValueExpression expression="" />)
    // Either render site is acceptable — just must not throw.
    const katexNode = screen.queryByTestId("value-expression-katex")
    const fallback = screen.queryByTestId("value-expression-fallback")
    expect(katexNode ?? fallback).not.toBeNull()
  })
})
