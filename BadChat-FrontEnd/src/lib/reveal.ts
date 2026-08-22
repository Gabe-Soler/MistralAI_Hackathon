import type { CSSProperties } from 'react'

import { stagger } from './stagger'

/**
 * How a list item appears, chosen by whether the run is finished or still arriving.
 *
 * `stagger` gives each item a slice of a shared scroll variable, which is exactly right
 * for a completed run: the whole list exists, and scrolling deals it out in order. It
 * cannot work for a live one. Its per-item threshold is derived from `count`, so every
 * visible item's slice shifts as the list grows and items jump backwards in their fade;
 * and reveal is gated on scroll position, so anything arriving while the reader sits at
 * the top renders at opacity 0 and is simply invisible.
 *
 * Live items therefore animate on mount instead. Nothing about `stagger` changes, and the
 * landing page keeps the scroll choreography it was designed around.
 */
export type RevealMode = 'scroll' | 'live'

interface RevealOptions {
  fade?: number
  lift?: string
}

export function reveal(
  index: number,
  count: number,
  progress: string,
  mode: RevealMode,
  opts: RevealOptions = {},
): CSSProperties {
  if (mode === 'scroll') return stagger(index, progress, count, opts)
  // Mount-triggered, so it fires when the event lands rather than when the page scrolls.
  return { animation: 'reveal-in 320ms ease-out both' }
}
