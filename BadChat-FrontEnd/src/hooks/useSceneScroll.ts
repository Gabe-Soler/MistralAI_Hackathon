import { useEffect } from 'react'

/**
 * Scroll distances in viewports rather than pixels, so the timing is identical
 * on a short laptop and a tall monitor.
 *
 * The hero is pinned for exactly as long as the cat's run, which is what lets
 * the run be slow: the cat would otherwise be carried off the top of the screen
 * before it ever reached the right edge. Raise CAT_RUN for a longer, lazier
 * crossing — the pin, the page height and every later cue follow it.
 */
const CAT_RUN = 1

/** Beat after the cat exits before the run log starts streaming in. */
const STEPS_DELAY = 0.25

/**
 * Derived, never hand-tuned: each stage is cued off the end of the last, so
 * retiming the cat can't leave the log streaming over a cat still on screen,
 * or the findings landing before the run that found them.
 */
const STEPS_IN = { start: CAT_RUN + STEPS_DELAY, end: CAT_RUN + STEPS_DELAY + 0.6 }

/**
 * The findings land only once the run log has finished. Each card crosses its
 * own slice of this range (see QaIssueCard), so they arrive in sequence.
 *
 * STEPS_IN must end before the first card scrolls into view, or the gate opens
 * on a card that is already on screen and it sits there blank. Lengthening the
 * log, or moving the findings up the page, needs a check that it still does.
 */
const CARDS_IN = { start: STEPS_IN.end, end: STEPS_IN.end + 0.75 }

/** Viewports of scroll the hero stays pinned — the length of the cat's run. */
export const HERO_PIN = CAT_RUN

/** Total scroll the scene needs, with a little left over past the last card. */
export const SCENE_SCROLL = CARDS_IN.end + 0.1

const clamp01 = (n: number) => (n < 0 ? 0 : n > 1 ? 1 : n)

/**
 * Publishes scroll progress as CSS custom properties on the document root:
 *
 * - `--cat-run`   0 → 1 as the cat crosses the screen
 * - `--steps-in`  0 → 1 once the cat has left, streaming in the run log
 * - `--cards-in`  0 → 1 after that, dealing out the QA findings
 *
 * Everything downstream is plain CSS reading those variables, so scrolling
 * never triggers a React render and the scenes stay in step without any shared
 * state between them.
 */
export function useSceneScroll() {
  useEffect(() => {
    const root = document.documentElement
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')

    const write = (run: number, steps: number, cards: number) => {
      root.style.setProperty('--cat-run', run.toFixed(4))
      root.style.setProperty('--steps-in', steps.toFixed(4))
      root.style.setProperty('--cards-in', cards.toFixed(4))
    }

    let frame = 0
    const paint = () => {
      frame = 0
      const span = window.innerHeight || 1
      const y = window.scrollY
      /** Progress through a range given in viewports. */
      const through = ({ start, end }: { start: number; end: number }) =>
        clamp01((y - start * span) / ((end - start) * span))
      write(clamp01(y / (CAT_RUN * span)), through(STEPS_IN), through(CARDS_IN))
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(paint)
    }

    const sync = () => {
      cancelAnimationFrame(frame)
      frame = 0
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)

      if (motion.matches) {
        // Park the cat mid-run and leave the results in place, no scroll coupling.
        write(0.5, 1, 1)
        return
      }
      paint()
      window.addEventListener('scroll', onScroll, { passive: true })
      window.addEventListener('resize', onScroll, { passive: true })
    }

    sync()
    motion.addEventListener('change', sync)

    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      motion.removeEventListener('change', sync)
    }
  }, [])
}
