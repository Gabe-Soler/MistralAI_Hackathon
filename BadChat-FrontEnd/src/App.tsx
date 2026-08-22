import { CatRunner } from '@/components/CatRunner'
import { Hero } from '@/components/Hero'
import { HeroStaircase } from '@/components/HeroStaircase'

function App() {
  return (
    <>
      <Hero />

      {/* The staircase's reflection opens the next page, revealed on scroll. */}
      <section className="relative flex min-h-svh flex-col bg-[var(--hero-bg)]">
        <HeroStaircase half="bottom" />

        {/* The cat crosses the open middle of the page, below the staircase. */}
        <CatRunner className="top-1/2 -translate-y-1/2" />
      </section>
    </>
  )
}

export default App
