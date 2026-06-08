'use client'

import { useEffect, useState } from 'react'

type DoiStatus = 'idle' | 'validating' | 'valid' | 'invalid' | 'timeout'

interface DoiValidatorProps {
  doi: string
}

export default function DoiValidator({ doi }: DoiValidatorProps) {
  const [status, setStatus] = useState<DoiStatus>('idle')

  useEffect(() => {
    if (!doi || doi.trim().length === 0) {
      setStatus('idle')
      return
    }

    setStatus('validating')

    const timer = setTimeout(async () => {
      try {
        const res = await fetch('/api/tools/validate-doi', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ doi: doi.trim() }),
        })
        const data = await res.json()
        if (data.valid) {
          setStatus('valid')
        } else {
          setStatus('invalid')
        }
      } catch {
        setStatus('timeout')
      }
    }, 1000)

    return () => clearTimeout(timer)
  }, [doi])

  if (status === 'idle') return null

  const statusMap: Record<DoiStatus, { icon: string; text: string; cls: string }> = {
    idle: { icon: '', text: '', cls: '' },
    validating: { icon: '⏳', text: '验证中...', cls: 'text-gray-400' },
    valid: { icon: '✅', text: 'DOI 有效', cls: 'text-green-400' },
    invalid: { icon: '❌', text: 'DOI 无效', cls: 'text-red-400' },
    timeout: { icon: '⚠️', text: '验证超时', cls: 'text-yellow-400' },
  }

  const s = statusMap[status]

  return (
    <span className={`text-xs ml-2 ${s.cls}`}>
      {s.icon} {s.text}
    </span>
  )
}
