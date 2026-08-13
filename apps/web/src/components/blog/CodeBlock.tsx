"use client"

import { useState } from "react"

interface CodeBlockProps {
  readonly language?: string
  readonly children: string
}

export function CodeBlock({ language = "text", children }: CodeBlockProps) {
  const [isCopied, setIsCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(children)
      setIsCopied(true)
      setTimeout(() => setIsCopied(false), 2000)
    } catch (err) {
      console.error("Failed to copy code:", err)
    }
  }

  // Simple syntax highlighting based on language
  const getLanguageLabel = () => {
    const labels: Record<string, string> = {
      js: "JavaScript",
      ts: "TypeScript",
      py: "Python",
      java: "Java",
      cpp: "C++",
      c: "C",
      go: "Go",
      rs: "Rust",
      rb: "Ruby",
      php: "PHP",
      sql: "SQL",
      sh: "Shell",
      bash: "Bash",
      json: "JSON",
      yaml: "YAML",
      xml: "XML",
      html: "HTML",
      css: "CSS",
      md: "Markdown",
      txt: "Text",
    }
    return labels[language] || language
  }

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-block-language">{getLanguageLabel()}</span>
        <button
          type="button"
          className="code-block-copy"
          onClick={handleCopy}
          aria-label={isCopied ? "已复制" : "复制代码"}
        >
          {isCopied ? "✓" : "复制"}
        </button>
      </div>
      <pre className="code-block-pre">
        <code className={`language-${language}`}>{children}</code>
      </pre>
    </div>
  )
}
