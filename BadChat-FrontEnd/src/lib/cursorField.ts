import { useEffect, useState } from 'react'

/**
 * One shared pointer loop for everything that follows the cursor.
 *
 * The cat and the backdrop both need the same smoothed position on the same
 * frame — if each ran its own spring they would drift apart and the ripple
 * would stop landing under the cat. So a single rAF loop owns the smoothing
 * and publishes a frame to every subscriber, in the same style as
 * useSceneScroll: no React state, no re-render, just a paint.
 */

export interface CursorFrame {
  /** Smoothed position in client px — where the cat is actually drawn. */
  x: number
  y: number
  /** Movement of the smoothed position over the last frame, in client px. */
  vx: number
  vy: number
  /** 1 while the pointer is over the page, easing to 0 once it leaves. */
  strength: number
  /** Seconds since the loop woke, for anything time-driven (the ripple). */
  time: number
}

type Listener = (frame: CursorFrame) => void

const FOLLOW = 0.2 // per 60fps frame, toward the real pointer
const FADE = 0.08 // per 60fps frame, toward the target strength

const subs = new Set<Listener>()

let rawX = 0
let rawY = 0
let placed = false // the pointer has been seen at least once
let inside = false

const frame: CursorFrame = { x: 0, y: 0, vx: 0, vy: 0, strength: 0, time: 0 }

let raf = 0
let last = 0
let started = 0

/** Frame-rate independent lerp factor for a per-60fps-frame rate. */
const ease = (rate: number, dt: number) => 1 - Math.pow(1 - rate, dt / (1000 / 60))

function tick(now: number) {
  raf = 0
  const dt = Math.min(64, now - last)
  last = now

  const k = ease(FOLLOW, dt)
  const nx = frame.x + (rawX - frame.x) * k
  const ny = frame.y + (rawY - frame.y) * k
  frame.vx = nx - frame.x
  frame.vy = ny - frame.y
  frame.x = nx
  frame.y = ny
  frame.strength += ((inside ? 1 : 0) - frame.strength) * ease(FADE, dt)
  frame.time = (now - started) / 1000

  for (const fn of subs) fn(frame)

  // Keep going while the pointer is here, and afterwards only long enough for
  // the fade-out to finish — an idle tab costs nothing.
  if (inside || frame.strength > 0.002) raf = requestAnimationFrame(tick)
  else {
    frame.strength = 0
    for (const fn of subs) fn(frame)
  }
}

function wake() {
  if (raf || !subs.size) return
  last = performance.now()
  if (!started) started = last
  raf = requestAnimationFrame(tick)
}

function onMove(e: PointerEvent) {
  rawX = e.clientX
  rawY = e.clientY
  if (!placed) {
    // First sighting: start where the pointer already is, or the cat flies in
    // from the top-left corner.
    placed = true
    frame.x = rawX
    frame.y = rawY
  }
  inside = true
  wake()
}

function onLeave() {
  inside = false
  wake()
}

function attach() {
  window.addEventListener('pointermove', onMove, { passive: true })
  window.addEventListener('pointerdown', onMove, { passive: true })
  document.addEventListener('pointerleave', onLeave)
  window.addEventListener('blur', onLeave)
}

function detach() {
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerdown', onMove)
  document.removeEventListener('pointerleave', onLeave)
  window.removeEventListener('blur', onLeave)
  cancelAnimationFrame(raf)
  raf = 0
}

/** Receive a frame for as long as the pointer is live. Returns an unsubscribe. */
export function subscribeCursor(fn: Listener): () => void {
  if (!subs.size) attach()
  subs.add(fn)
  wake()
  return () => {
    subs.delete(fn)
    if (!subs.size) detach()
  }
}

const QUERY = '(pointer: fine) and (prefers-reduced-motion: no-preference)'

/**
 * Whether to run the cursor at all: a mouse to run it with, and no request to
 * cut motion. Both are live — plugging in a mouse, or turning reduced motion
 * off, brings the cat back without a reload.
 */
export function useCursorField(): boolean {
  const [on, setOn] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(QUERY).matches,
  )
  useEffect(() => {
    const mq = window.matchMedia(QUERY)
    const sync = () => setOn(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])
  return on
}
