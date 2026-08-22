import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { useScrollPalette } from '@/hooks/useScrollPalette'
import {
  BOTTOM_CELLS,
  COL,
  COLS,
  FOLD,
  ROW,
  SLOTS,
  TOP_CELLS,
  offsetFor,
} from '@/components/heroGrid'

export interface HeroStaircaseProps {
  /** `top` closes the hero; `bottom` is its reflection opening the next section. */
  half: 'top' | 'bottom'
  className?: string
  /** Pixels of scrolling that complete one full trip through the palette. */
  cycleLength?: number
  /** Anything standing on the steps, positioned against this box. */
  children?: ReactNode
}

export function HeroStaircase({
  half,
  className,
  cycleLength,
  children,
}: HeroStaircaseProps) {
  const ref = useScrollPalette<SVGSVGElement>({ slots: SLOTS, offsetFor, cycleLength })
  const cells = half === 'top' ? TOP_CELLS : BOTTOM_CELLS

  return (
    // @container so anything standing on the steps can size and travel in
    // `cqw` — the staircase's own width, independent of the scrollbar.
    <div className={cn('@container relative w-full', className)}>
      <svg
        ref={ref}
        // Stacked above `children`, so anything walking the steps is occluded
        // by the blocks instead of sliding over them.
        className="relative z-10 block h-auto w-full"
        viewBox={`0 0 ${COLS * COL} ${FOLD * ROW}`}
        preserveAspectRatio="xMidYMid meet"
        fill="none"
        // Axis-aligned blocks on a fractional row grid: snap edges so abutting
        // rects never show an antialiased hairline between them.
        shapeRendering="crispEdges"
        aria-hidden="true"
        focusable="false"
      >
        {cells.map((cell) => (
          <rect
            key={`${cell.col}-${cell.row}`}
            x={cell.col * COL}
            y={cell.row * ROW}
            width={cell.span * COL}
            height={cell.span * ROW}
            fill={
              cell.accent
                ? `var(--hg-${cell.slot}, var(--hero-accent))`
                : 'var(--hero-neutral)'
            }
          />
        ))}
      </svg>
      {children}
    </div>
  )
}
