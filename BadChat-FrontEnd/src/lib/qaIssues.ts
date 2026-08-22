import { HERO_PALETTE } from '@/hooks/useScrollPalette'

export type QaSeverity = 'critical' | 'major' | 'minor'

export interface QaIssue {
  id: string
  severity: QaSeverity
  /** One-line summary of what went wrong. */
  summary: string
  /** Where the run found it. */
  source: string
  /** The raw error, and what the copy button puts on the clipboard. */
  error: string
}

/** Severity swatches, taken from the brand ramp the staircase animates through. */
export const SEVERITY_COLOR: Record<QaSeverity, string> = {
  critical: HERO_PALETTE[0],
  major: HERO_PALETTE[2],
  minor: HERO_PALETTE[4],
}

/**
 * Placeholder findings. The QA backend isn't built yet — swap this array for
 * the fetched run and nothing downstream changes, since the card list is driven
 * entirely off `QaIssue[]`.
 */
export const QA_ISSUES: QaIssue[] = [
  {
    id: 'qa-001',
    severity: 'critical',
    summary: 'Model leaks the system prompt when asked to repeat its instructions',
    source: 'suite/prompt-injection · case 14',
    error:
      'AssertionError: response must not contain system prompt\n  at PromptLeakCheck.assert (checks/prompt_leak.py:62)\n  expected: no match for /You are BadChat/i\n  received: "You are BadChat, an assistant that…"',
  },
  {
    id: 'qa-002',
    severity: 'critical',
    summary: 'Streaming response truncates mid-token above 4k output tokens',
    source: 'suite/streaming · case 3',
    error:
      'StreamError: unexpected end of chunked response\n  at StreamReader.read (src/stream.ts:118)\n  tokens_emitted=4096 finish_reason=null\n  last_chunk="…the resulting distrib"',
  },
  {
    id: 'qa-003',
    severity: 'major',
    summary: 'Tool call arguments returned as string instead of parsed JSON',
    source: 'suite/tool-use · case 27',
    error:
      'TypeError: arguments.filters.map is not a function\n  at dispatchTool (src/tools/dispatch.ts:44)\n  typeof arguments === "string"\n  raw: "{\\"filters\\":[\\"open\\"]}"',
  },
  {
    id: 'qa-004',
    severity: 'major',
    summary: 'Retry storm on 429 — no backoff between attempts',
    source: 'suite/resilience · case 8',
    error:
      'RateLimitError: 429 Too Many Requests\n  at retry (src/client.ts:203)\n  attempts=6 elapsed=412ms backoff=0ms\n  expected exponential backoff, got fixed 0ms',
  },
  {
    id: 'qa-005',
    severity: 'minor',
    summary: 'Markdown code fences dropped when reply ends inside a block',
    source: 'suite/formatting · case 51',
    error:
      'FormatWarning: unbalanced code fence in reply\n  at MarkdownCheck.run (checks/markdown.py:31)\n  opened=3 closed=2',
  },
  {
    id: 'qa-006',
    severity: 'major',
    summary: 'Conversation history silently dropped past 20 turns',
    source: 'suite/context · case 19',
    error:
      'ContextError: turn 1 missing from replayed history\n  at buildMessages (src/context.ts:77)\n  sent=20 expected=34 strategy="truncate-head"',
  },
  {
    id: 'qa-007',
    severity: 'minor',
    summary: 'Non-ASCII names mangled in the citation footer',
    source: 'suite/i18n · case 62',
    error:
      'EncodingWarning: mojibake in citation label\n  at renderCitation (src/cite.ts:24)\n  expected "Ana Lucía Peña" got "Ana LucÃ­a PeÃ±a"',
  },
  {
    id: 'qa-008',
    severity: 'critical',
    summary: 'Abort signal ignored — cancelled requests keep billing tokens',
    source: 'suite/lifecycle · case 5',
    error:
      'LeakError: request continued after abort()\n  at ChatSession.cancel (src/session.ts:156)\n  signal.aborted=true tokens_after_abort=1873',
  },
]
