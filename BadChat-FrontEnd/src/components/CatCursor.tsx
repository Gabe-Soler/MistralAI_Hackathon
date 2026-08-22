import { useEffect, useRef } from 'react'
import { subscribeCursor, useCursorField } from '@/lib/cursorField'
import { EVIL_CAT, fitCat } from '@/lib/cats'

/**
 * Replaces the pointer with the cat.
 *
 * It trails the real pointer by a frame or two — the smoothing lives in
 * cursorField, shared with the backdrop so the ripple always breaks under the
 * cat rather than beside it — and leans into the direction it's being thrown.
 *
 * Only ever mounted where there's a mouse to hide and no request for reduced
 * motion; on touch and in reduced motion the native cursor is left alone.
 */

/** Width of the sprite in CSS px; height follows the cat's own ratio. */
const SIZE = 46

/**
 * Where the pointer sits inside the sprite, as a fraction of it: just inside
 * the top-left, on the ear, so the cat hangs off the point the way an arrow's
 * body hangs off its tip.
 */
const HOT_X = 0.2
const HOT_Y = 0.1

const MAX_TILT = 14

export function CatCursor() {
  const live = useCursorField()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!live) return
    const el = ref.current
    if (!el) return

    const root = document.documentElement
    root.classList.add('cat-cursor')

    const stop = subscribeCursor(({ x, y, vx, vy, strength }) => {
      const tilt = Math.max(-MAX_TILT, Math.min(MAX_TILT, vx * 0.6))
      // Stretches a little when thrown across the screen, so a fast flick
      // reads as the cat being dragged rather than teleporting.
      const speed = Math.min(1, Math.hypot(vx, vy) / 28)
      el.style.transform = `translate3d(${x}px, ${y}px, 0) rotate(${tilt}deg) scale(${1 + speed * 0.16})`
      el.style.opacity = `${strength}`
    })

    return () => {
      stop()
      root.classList.remove('cat-cursor')
    }
  }, [live])

  if (!live) return null

  const height = SIZE * (EVIL_CAT.content.h / EVIL_CAT.content.w)

  return (
    <div
      ref={ref}
      aria-hidden="true"
      // Hidden until the pointer is first seen, so it never flashes at 0,0.
      className="pointer-events-none fixed left-0 top-0 z-[999] opacity-0 will-change-transform"
      style={{
        width: SIZE,
        height,
        marginLeft: -SIZE * HOT_X,
        marginTop: -height * HOT_Y,
        transformOrigin: `${HOT_X * 100}% ${HOT_Y * 100}%`,
        filter: 'drop-shadow(2px 3px 0 var(--hero-shadow))',
      }}
    >
      <img src={EVIL_CAT.src} alt="" style={fitCat(EVIL_CAT)} />
    </div>
  )
}
