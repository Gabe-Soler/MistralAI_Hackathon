import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import { LIGHTER, PALETTE_RGB, buildScene } from '@/lib/pixelScene'
import { subscribeCursor, useCursorField } from '@/lib/cursorField'

/**
 * The pixel-art sunset behind the hero, and the cursor's effect on it.
 *
 * The canvas is sized in *cells*, not device pixels — a couple of hundred
 * across — and CSS blows it up with `image-rendering: pixelated`. So the chunky
 * look is the real resolution rather than a filter over a smooth image, and a
 * frame of work is a few thousand array writes instead of a few million.
 *
 * The cursor doesn't move sprites around: it resamples the scene through a
 * displacement. Each cell within reach reads its colour from a neighbour
 * pushed along a decaying ripple, dragged by the pointer's velocity, so the
 * artwork visibly shoves aside as the cat walks over it and settles behind it.
 */

/** CSS px per cell, and the grid width it is allowed to resolve to. */
const CELL = 6
const MIN_COLS = 96
const MAX_COLS = 260

/** The disturbance, in cells (so it scales with the art, not the screen). */
const REACH = 22
const AMP = 2.4
const WAVE = 0.5 // radians per cell — roughly 12-cell rings
const SPEED = 5.5 // radians per second, travelling outward
const SMEAR = 0.11 // cells of drag per cell/frame of pointer velocity
const SCORCH = 0.5 // falloff above which cells step one shade lighter

const clamp = (n: number, lo: number, hi: number) => (n < lo ? lo : n > hi ? hi : n)

export function PixelBackdrop({ className }: { className?: string }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const live = useCursorField()

  useEffect(() => {
    const host = hostRef.current
    const canvas = canvasRef.current
    if (!host || !canvas) return
    const ctx = canvas.getContext('2d', { alpha: false })
    if (!ctx) return

    let w = 0
    let h = 0
    let base: Uint8Array<ArrayBufferLike> = new Uint8Array(0)
    let data: Uint8ClampedArray<ArrayBufferLike> = new Uint8ClampedArray(0)
    let image: ImageData | null = null

    /** The host's box, cached — read per frame it would thrash layout. */
    let box = { left: 0, top: 0, width: 0, height: 0 }
    const measure = () => {
      const r = host.getBoundingClientRect()
      box = { left: r.left, top: r.top, width: r.width, height: r.height }
      return r
    }

    const write = (x: number, y: number, colour: number) => {
      const p = (y * w + x) * 4
      const c = colour * 3
      data[p] = PALETTE_RGB[c]
      data[p + 1] = PALETTE_RGB[c + 1]
      data[p + 2] = PALETTE_RGB[c + 2]
      data[p + 3] = 255
    }

    // The region the last frame disturbed, so it can be handed back to the
    // untouched scene before the next one is drawn.
    let dx0 = 0
    let dy0 = 0
    let dx1 = 0
    let dy1 = 0

    const resize = () => {
      const r = measure()
      if (!r.width || !r.height) return
      const cols = clamp(Math.round(r.width / CELL), MIN_COLS, MAX_COLS)
      const rows = Math.max(1, Math.round((cols * r.height) / r.width))
      if (cols === w && rows === h) return

      w = cols
      h = rows
      canvas.width = w
      canvas.height = h
      base = buildScene(w, h)
      image = ctx.createImageData(w, h)
      data = image.data
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) write(x, y, base[y * w + x])
      ctx.putImageData(image, 0, 0)
      dx0 = dy0 = dx1 = dy1 = 0
    }

    resize()

    const ro = new ResizeObserver(resize)
    ro.observe(host)
    const onScroll = () => measure()
    window.addEventListener('scroll', onScroll, { passive: true })

    let stop = () => {}
    if (live) {
      stop = subscribeCursor(({ x, y, vx, vy, strength, time }) => {
        if (!image || !box.width || !box.height) return

        // Restore whatever the last frame pushed around.
        for (let py = dy0; py < dy1; py++)
          for (let px = dx0; px < dx1; px++) write(px, py, base[py * w + px])
        const px0 = dx0
        const py0 = dy0
        const px1 = dx1
        const py1 = dy1

        const cx = ((x - box.left) / box.width) * w
        const cy = ((y - box.top) / box.height) * h
        const vcx = (vx / box.width) * w
        const vcy = (vy / box.height) * h

        if (strength > 0.002) {
          const pad = REACH + AMP + 2
          dx0 = clamp(Math.floor(cx - pad), 0, w)
          dy0 = clamp(Math.floor(cy - pad), 0, h)
          dx1 = clamp(Math.ceil(cx + pad), 0, w)
          dy1 = clamp(Math.ceil(cy + pad), 0, h)

          for (let py = dy0; py < dy1; py++) {
            const oy = py - cy
            for (let ppx = dx0; ppx < dx1; ppx++) {
              const ox = ppx - cx
              const d = Math.sqrt(ox * ox + oy * oy)
              if (d > REACH) continue // already sitting at its resting colour
              const fall = 1 - d / REACH
              const amount = fall * fall * strength
              const push = AMP * amount * Math.sin(d * WAVE - time * SPEED)
              const inv = d > 0.001 ? 1 / d : 0
              const sx = clamp(
                Math.round(ppx + ox * inv * push - vcx * amount * SMEAR),
                0,
                w - 1,
              )
              const sy = clamp(
                Math.round(py + oy * inv * push - vcy * amount * SMEAR),
                0,
                h - 1,
              )
              const colour = base[sy * w + sx]
              write(ppx, py, amount > SCORCH ? LIGHTER[colour] : colour)
            }
          }
        } else {
          dx0 = dy0 = dx1 = dy1 = 0
        }

        // One upload covering both what was undone and what was just drawn.
        const ux0 = Math.min(px0, dx0 || px0)
        const uy0 = Math.min(py0, dy0 || py0)
        const ux1 = Math.max(px1, dx1)
        const uy1 = Math.max(py1, dy1)
        if (ux1 > ux0 && uy1 > uy0)
          ctx.putImageData(image, 0, 0, ux0, uy0, ux1 - ux0, uy1 - uy0)
      })
    }

    return () => {
      stop()
      ro.disconnect()
      window.removeEventListener('scroll', onScroll)
    }
  }, [live])

  return (
    <div
      ref={hostRef}
      aria-hidden="true"
      className={cn('absolute inset-0 overflow-hidden', className)}
    >
      <canvas ref={canvasRef} className="block h-full w-full [image-rendering:pixelated]" />
    </div>
  )
}
