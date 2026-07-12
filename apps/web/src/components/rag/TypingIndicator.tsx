'use client'

/**
 * TypingIndicator — three bouncing dots shown while the assistant is thinking.
 *
 * Uses CSS keyframes (compositor-friendly: transform + opacity only).
 * Spec: NFM-848 §2.2
 */

export function TypingIndicator() {
  return (
    <div className="flex items-center gap-1.5 px-4 py-3" role="status" aria-label="正在回复">
      <span className="sr-only">正在回复</span>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="block w-2 h-2 rounded-full bg-gray-400 animate-[bounce_1.4s_ease-in-out_infinite]"
          aria-hidden="true"
          style={{ animationDelay: `${i * 0.16}s` }}
        />
      ))}
    </div>
  )
}
