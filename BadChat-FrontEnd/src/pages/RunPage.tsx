import { CatCursor } from "@/components/CatCursor";
import { CatFigure } from "@/components/CatFigure";
import { HeroStaircase } from "@/components/HeroStaircase";
import { QaResults } from "@/components/QaResults";
import { ThinkingTranscript } from "@/components/ThinkingTranscript";
import { useRun } from "@/hooks/useRun";
import { runHealth, toIssues, toThinkingSteps } from "@/lib/run/adapters";

/**
 * One run, live.
 *
 * Deliberately does NOT call useSceneScroll. The landing page's reveal is driven by
 * scroll position, which is right for a finished list and wrong for an arriving one --
 * anything landing while the reader sits at the top would render at opacity 0. Here the
 * items animate on mount instead (mode="live"), and the page takes its natural height
 * rather than the fixed SCENE_SCROLL viewports, because the transcript grows.
 *
 * No pinned hero either: the cat's run is a title sequence, and a run already in flight
 * should not make you scroll past one to see it.
 */
export function RunPage({ runId }: { runId: string }) {
  const view = useRun(runId);
  const health = runHealth(view);
  const issues = toIssues(view);
  const failed = view.phase === "failed";

  return (
    <>
      <CatCursor />

      <section className="flex min-h-svh flex-col bg-[var(--hero-bg)]">
        <HeroStaircase half="bottom" />

        <div className="flex flex-1 flex-col items-center px-[6vw] pt-[8vh] pb-[12vh]">
          <div className="mb-12 flex w-full items-center justify-center border-b">
            <CatFigure width="164px" className="mt-1 mb-0 shrink-0" />
          </div>

          <div className="relative flex w-full max-w-5xl items-start gap-18">
            <div className="min-w-0 flex-1">
              <div className="mb-6 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
                <p className="m-0 font-mono text-[0.7rem] tracking-[0.18em] text-[var(--muted-foreground)] uppercase">
                  BadChat · QA run
                </p>
                {/* Whether the run can be believed at all. A failed run proves nothing,
                    and "0 findings" beside "8 steps errored" is the distinction the tool
                    exists to make -- so it sits next to the findings, not in a log. */}
                <p
                  className="m-0 font-mono text-[0.7rem] tracking-[0.12em] uppercase"
                  style={{
                    color: failed || !health.ok
                      ? "var(--destructive)"
                      : "var(--muted-foreground)",
                  }}
                >
                  {view.connected || view.phase === "done" || failed ? health.text : "connecting…"}
                </p>
              </div>

              <ThinkingTranscript steps={toThinkingSteps(view)} mode="live" />

              {issues.length > 0 && (
                <div className="mt-8">
                  <QaResults issues={issues} mode="live" />
                </div>
              )}

              {view.phase === "done" && issues.length === 0 && (
                <p className="mt-8 font-mono text-sm text-[var(--muted-foreground)]">
                  No cross-tenant leaks found.
                  {health.ok ? "" : " Some steps errored, so this is not a clean result."}
                </p>
              )}
            </div>
          </div>
        </div>
      </section>

      <footer className="bg-[var(--hero-bg)]">
        <HeroStaircase half="top" />
      </footer>
    </>
  );
}
