import { HeroStaircase } from '@/components/HeroStaircase'
import { PixelBackdrop } from '@/components/PixelBackdrop'
import { FIRST_STEP_SURFACE } from '@/components/heroGrid'
import { WALKING_CAT, catAspect, fitCat } from '@/lib/cats'

/** The cat's width, as a fraction of the staircase's width. */
const CAT_WIDTH = '11%'

/**
 * Carries the cat from fully off the left edge to fully off the right one:
 * `100cqw` is the staircase's width, `100%` the cat's own. Driven entirely by
 * `--cat-run`, which useSceneScroll writes from scroll position.
 */
const CAT_TRAVEL = 'translateX(calc(var(--cat-run, 0) * (100cqw + 100%) - 100%))'

export function Hero() {
  return (
    <section className="relative flex min-h-svh flex-col overflow-hidden bg-[var(--hero-bg)]">
      {/* The sunset the rest of the hero stands in front of. It reacts to the
          cursor, so everything above it is stacked out of the way. */}
      <PixelBackdrop />

      <div className="relative flex flex-1 flex-col items-center justify-end px-6 pb-[11vh] text-center">
        <h1
          className="m-0 font-mono font-normal leading-[0.9] tracking-[-0.02em] text-[var(--hero-ink)]"
          style={{ fontSize: 'clamp(3.25rem, 12vw, 15rem)' }}
        >
          BadChat
        </h1>
        <p
          className="m-0 mt-[4vh] font-mono tracking-[0.12em] text-[var(--hero-ink)] opacity-75"
          style={{ fontSize: 'clamp(0.875rem, 2.4vw, 2rem)' }}
        >
          Mistral AI
        </p>
      </div>

      <HeroStaircase half="top" className="relative">
        {/* Runs the plateau — the same surface the cat used to sit on. It
            passes behind the taller steps at either end, clear of the text. */}
        <div
          className="absolute will-change-transform"
          style={{
            width: CAT_WIDTH,
            aspectRatio: catAspect(WALKING_CAT),
            bottom: `${FIRST_STEP_SURFACE * 100}%`,
            transform: CAT_TRAVEL,
          }}
        >
          <div
            aria-hidden="true"
            className="absolute bottom-0 left-1/2 h-[9%] w-[78%] -translate-x-1/2 translate-y-[45%] rounded-[50%]"
            style={{
              background:
                'radial-gradient(closest-side, var(--hero-shadow), transparent)',
            }}
          />
          <img src={WALKING_CAT.src} alt="" aria-hidden="true" style={fitCat(WALKING_CAT)} />
        </div>
      </HeroStaircase>
    </section>
  )
}
