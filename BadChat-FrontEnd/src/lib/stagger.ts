import type { CSSProperties } from 'react'

/**
 * A per-item slice of a 0 → 1 scroll variable, so a list can deal itself out in
 * sequence with no React state and no per-frame renders — each item reads its
 * own window out of the shared progress.
 *
 * The step is derived from `count` rather than fixed: the last item then always
 * lands exactly at progress 1, so adding steps or findings can never push the
 * tail of the list past the end of its range and leave it stuck part-faded.
 */
export function stagger(
  index: number,
  progress: string,
  count: number,
  { fade = 0.3, lift = '0.75rem' } = {},
): CSSProperties {
  const step = count > 1 ? (1 - fade) / (count - 1) : 0
  return {
    '--item-p': `clamp(0, calc((var(${progress}, 0) - ${(index * step).toFixed(4)}) / ${fade}), 1)`,
    opacity: 'var(--item-p)',
    transform: `translateY(calc((1 - var(--item-p)) * ${lift}))`,
  } as CSSProperties
}
