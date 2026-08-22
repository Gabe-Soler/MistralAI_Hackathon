import { cn } from '@/lib/utils'

/**
 * Width of the cat as a fraction of the viewport, so it holds its scale
 * against the staircase artwork. The gif is 4:3, with the cat inset in it.
 */
const CAT_WIDTH = 'clamp(7rem, 14vw, 16rem)'

/** One crossing of the page. The gif's walk cycle is 2.8s — this is five. */
const CROSSING = '14s'

export interface CatRunnerProps {
  /** Where the run sits in its positioned ancestor. */
  className?: string
}

/**
 * A cat that trots across the page from left to right, entering off one edge
 * and leaving by the other, on a loop. It clips its own track, so it never
 * widens the page.
 */
export function CatRunner({ className }: CatRunnerProps) {
  return (
    <div
      className={cn(
        'pointer-events-none absolute inset-x-0 overflow-hidden',
        className,
      )}
      style={{ height: `calc(${CAT_WIDTH} * 0.75)` }}
      aria-hidden="true"
    >
      <img
        src="/cat-walking-white.gif"
        alt=""
        className="cat-run absolute top-0 left-0 h-full w-auto max-w-none select-none"
        style={{ animationDuration: CROSSING }}
      />
    </div>
  )
}
