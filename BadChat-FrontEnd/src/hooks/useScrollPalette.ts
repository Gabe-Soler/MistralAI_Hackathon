import { useEffect, useRef } from 'react'

/**
 * The BadChat accent ramp. Index 4 (#FF9533) is the colour the static artwork
 * ships with, so the ramp is entered there — the page at rest matches the
 * exported SVG, and scrolling walks the rest of the loop.
 */
export const HERO_PALETTE = [
  '#D70006',
  '#FB0000',
  '#FF3B00',
  '#FF7900',
  '#FF9533',
] as const

/** Phase offset (in cycles) so the ramp starts on #FF9533 at scrollY = 0. */
const RAMP_ORIGIN = 4 / HERO_PALETTE.length

/**
 * Snap to a palette entry — no interpolation. Colours cut hard from one to the
 * next, so the artwork flips between the five brand colours rather than
 * sliding through the blends between them.
 */
function stepRamp(t: number): string {
  const n = HERO_PALETTE.length
  const wrapped = (((t % 1) + 1) % 1) * n
  return HERO_PALETTE[Math.floor(wrapped) % n]
}

export interface ScrollPaletteOptions {
  /** How many CSS variables to drive: `--hg-0` … `--hg-{slots-1}`. */
  slots: number
  /** Cycle offset applied to slot `i`, spreading the ramp across the artwork. */
  offsetFor: (slot: number) => number
  /** Pixels of scrolling that complete one full trip through the palette. */
  cycleLength?: number
  /**
   * Pixels over which the per-slot stagger fades in. At rest every slot shares
   * one colour (matching the static artwork); the steps break apart as you
   * scroll, so tiers stop flipping in unison.
   */
  spreadLength?: number
}

/**
 * Drives a set of `--hg-*` custom properties from scroll position.
 *
 * Values are written straight to the element's inline style inside a rAF, so
 * scrolling never triggers a React render — only a paint.
 */
export function useScrollPalette<T extends Element & ElementCSSInlineStyle>({
  slots,
  offsetFor,
  cycleLength = 1000,
  spreadLength = 400,
}: ScrollPaletteOptions) {
  const ref = useRef<T>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const offsets = Array.from({ length: slots }, (_, i) => offsetFor(i))

    const paintAt = (t: number, spread: number) => {
      for (let i = 0; i < slots; i++) {
        el.style.setProperty(`--hg-${i}`, stepRamp(t + offsets[i] * spread))
      }
    }

    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')

    let frame = 0
    const paint = () => {
      frame = 0
      const y = window.scrollY
      paintAt(RAMP_ORIGIN + y / cycleLength, Math.min(1, y / spreadLength))
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(paint)
    }

    const sync = () => {
      cancelAnimationFrame(frame)
      frame = 0
      window.removeEventListener('scroll', onScroll)

      if (motion.matches) {
        // Hold the artwork on its designed colour; no scroll coupling at all.
        paintAt(RAMP_ORIGIN, 0)
        return
      }
      paint()
      window.addEventListener('scroll', onScroll, { passive: true })
    }

    sync()
    motion.addEventListener('change', sync)

    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('scroll', onScroll)
      motion.removeEventListener('change', sync)
    }
  }, [slots, offsetFor, cycleLength, spreadLength])

  return ref
}
