import { CatFigure } from '@/components/CatFigure'
import { Hero } from '@/components/Hero'
import { HeroStaircase } from '@/components/HeroStaircase'
import { QaResults } from '@/components/QaResults'
import { ThinkingTranscript } from '@/components/ThinkingTranscript'
import { HERO_PIN, SCENE_SCROLL, useSceneScroll } from '@/hooks/useSceneScroll'

function App() {
  useSceneScroll()

  return (
    <>
      {/* Taller than the hero by exactly the cat's run, so the sticky child
          stays pinned for that long and the cat crosses a frozen screen. When
          it releases, the hero rests on this block's bottom edge — flush with
          the next section, so the staircase halves still meet with no seam. */}
      <div style={{ height: `calc(${1 + HERO_PIN} * 100svh)` }}>
        <div className="sticky top-0 h-svh">
          <Hero />
        </div>
      </div>

      {/* The staircase's reflection opens the next page. Below it the run reads
          as a single assistant turn: the log it worked through, then what it
          found. There is no conversation — only the answer. */}
      <section
        className="flex flex-col bg-[var(--hero-bg)]"
        style={{ minHeight: `calc(${SCENE_SCROLL - HERO_PIN} * 100svh)` }}
      >
        <HeroStaircase half="bottom" />

        <div className="flex-1 px-[6vw] pt-[10vh] pb-[12vh] flex items-center flex-col">
                      <div className='border-b w-full flex items-center justify-center mb-12'>
                        <CatFigure width="164px" className="mt-1 shrink-0 mb-0" />
                      </div>

          <div className="flex w-full max-w-5xl items-start gap-18 relative">

            <div className="min-w-0 flex-1">
              <p className="m-0 mb-6 font-mono text-[0.7rem] tracking-[0.18em] text-[var(--muted-foreground)] uppercase">
                BadChat · QA run
              </p>

              <ThinkingTranscript />

              <div className="mt-8">
                <QaResults />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Same artwork and colour ramp as the hero's opening block, closing the
          page out. */}
      <footer className="bg-[var(--hero-bg)]">
        <HeroStaircase half="top" />
      </footer>
    </>
  )
}

export default App
