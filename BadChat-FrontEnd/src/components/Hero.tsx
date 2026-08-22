import { HeroStaircase } from '@/components/HeroStaircase'
import { FIRST_STEP_SURFACE, HALF_ASPECT } from '@/components/heroGrid'

/**
 * animated-sitting-cat.gif is a 896² frame that is mostly transparent — the cat
 * only occupies the box below, measured as the union across all 27 frames so
 * the tail's full swing is included. Without compensating for that padding the
 * image box would sit on the step while the cat appeared to float above it.
 */
const CAT_CONTENT = { left: 32 / 896, bottom: 1 - 608 / 896, width: 704 / 896 }

/** Where the cat should stand, as fractions of the staircase's width. */
const CAT_TARGET = { left: 0.564, width: 0.171 }

/** Scale the frame up so its *content* spans the target width. */
const catFrameWidth = CAT_TARGET.width / CAT_CONTENT.width

const catStyle = {
  width: `${catFrameWidth * 100}%`,
  left: `${(CAT_TARGET.left - CAT_CONTENT.left * catFrameWidth) * 100}%`,
  // The frame is square, so its height in half-heights is width / aspect. Drop
  // it by its bottom padding to land the cat's feet on the step surface.
  bottom: `${(FIRST_STEP_SURFACE - CAT_CONTENT.bottom * (catFrameWidth / HALF_ASPECT)) * 100}%`,
}

export function Hero() {
  return (
    <section className="relative flex min-h-svh flex-col overflow-hidden bg-[var(--hero-bg)]">
      <div className="flex flex-1 flex-col items-center justify-end px-6 pb-[5vh] text-center">
        <h1
          className="m-0 font-mono font-normal leading-[0.9] tracking-[-0.02em] text-[var(--hero-ink)]"
          style={{ fontSize: 'clamp(3.25rem, 12vw, 15rem)' }}
        >
          BadUser
        </h1>
        <p
          className="m-0 mt-[4vh] font-mono tracking-[0.12em] text-[var(--hero-ink)] opacity-75"
          style={{ fontSize: 'clamp(0.875rem, 2.4vw, 2rem)' }}
        >
          Mistral AI
        </p>
      </div>

      <HeroStaircase half="top">
        <img
          src="/animated-sitting-cat.gif"
          alt=""
          aria-hidden="true"
          className="pointer-events-none absolute select-none"
          style={catStyle}
        />
      </HeroStaircase>
    </section>
  )
}
