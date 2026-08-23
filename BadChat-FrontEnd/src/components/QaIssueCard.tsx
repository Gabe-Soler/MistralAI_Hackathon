import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from '@phosphor-icons/react'
import { SEVERITY_COLOR, type QaIssue } from '@/lib/qaIssues'
import { reveal, type RevealMode } from '@/lib/reveal'

interface QaIssueCardProps {
  issue: QaIssue
  /** Position in the list, used to stagger the reveal. */
  index: number
  /** How many cards there are. Must come from the caller: a live list grows, and reading
      a module constant here staggered every run against the mock array's length. */
  count: number
  /** `scroll` deals a finished run out on scroll; `live` animates each card on arrival. */
  mode?: RevealMode
}

export function QaIssueCard({ issue, index, count, mode = 'scroll' }: QaIssueCardProps) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => () => clearTimeout(timer.current), [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issue.error)
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1600)
    } catch {
      // Clipboard blocked (insecure context or denied) — leave the label alone
      // rather than claiming a copy that never happened.
      setCopied(false)
    }
  }

  return (
    <article
      className="border border-[var(--border)] bg-[var(--card)] p-8 sm:p-10"
      // Each card crosses its own slice of --cards-in, so they land in sequence.
      style={reveal(index, count, '--cards-in', mode, { fade: 0.3, lift: '1.25rem' })}
    >
      <div className="flex items-center gap-4">
        {/* A flat block of colour, echoing the staircase's tiers. */}
        <span
          aria-hidden="true"
          className="h-2 w-6 shrink-0"
          style={{ background: SEVERITY_COLOR[issue.severity] }}
        />
        <span className="font-mono text-[0.7rem] tracking-[0.18em] uppercase">
          {issue.severity}
        </span>
        <span className="font-mono text-[0.7rem] tracking-wide opacity-50">
          {issue.source}
        </span>
      </div>

      <h3 className="m-0 mt-6 max-w-[46ch] text-xl leading-snug font-normal text-[var(--foreground)]">
        {issue.summary}
      </h3>

      {/* The error reads as plain type against the page, not a slab — the rule
          alone carries the severity. */}
      <pre className="mt-6 overflow-x-auto border-l pl-5 font-mono text-[0.75rem] leading-relaxed whitespace-pre opacity-60"
        style={{ borderLeftColor: SEVERITY_COLOR[issue.severity] }}
      >
        {issue.error}
      </pre>

      <div className="mt-8 flex items-center gap-4">
        {issue.shotUrl && (
        // A picture of the page a user could not get through IS the evidence for a
        // usability failure, the way a leaked canary is for a breach.
        <img
          src={issue.shotUrl}
          alt=""
          loading="lazy"
          className="mt-6 block w-full border border-[var(--border)]"
        />
      )}

      <button
          type="button"
          onClick={copy}
          className="flex items-center gap-2 border border-[var(--border)] px-3.5 py-2 font-mono text-[0.7rem] tracking-[0.12em] uppercase transition-colors hover:border-[var(--hero-ink)] hover:bg-[var(--muted)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
        >
          {copied ? <Check size={13} weight="bold" /> : <Copy size={13} />}
          {copied ? 'Copied' : 'Copy error'}
        </button>
        {/* Announce the copy without shifting the layout. */}
        <span aria-live="polite" className="sr-only">
          {copied ? 'Error copied to clipboard' : ''}
        </span>
      </div>
    </article>
  )
}
