import {
  ArrowsClockwise,
  FolderOpen,
  Play,
  Warning,
  WaveSine,
} from '@phosphor-icons/react'
import { CatMarkerIcon } from '@/components/CatFigure'
import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker'
import { THINKING_STEPS, type StepIcon, type ThinkingStep } from '@/lib/thinkingSteps'
import { reveal, type RevealMode } from '@/lib/reveal'

const ICONS: Record<StepIcon, typeof FolderOpen> = {
  files: FolderOpen,
  run: Play,
  retry: ArrowsClockwise,
  trace: WaveSine,
  found: Warning,
}

interface ThinkingTranscriptProps {
  /** Defaults to the placeholder log, so the landing page is unchanged. */
  steps?: ThinkingStep[]
  mode?: RevealMode
}

export function ThinkingTranscript({
  steps: items = THINKING_STEPS,
  mode = 'scroll',
}: ThinkingTranscriptProps) {
  return (
    <div className="flex flex-col gap-3">
      {items.map((step, index) => {
        const Icon = step.icon ? ICONS[step.icon] : null
        return (
          <div key={step.id} style={reveal(index, items.length, '--steps-in', mode)}>
            <Marker
              variant={step.variant}
              // Active work is announced to assistive tech, per the docs.
              role={step.thinking ? 'status' : undefined}
              className="text-[0.95rem]"
            >
              {/* The separator variant is a labelled rule — it takes no icon. */}
              {step.variant !== 'separator' &&
                (step.thinking ? (
                  <CatMarkerIcon />
                ) : (
                  Icon && (
                    <MarkerIcon className="flex size-5 items-center justify-center">
                      <Icon size={17} />
                    </MarkerIcon>
                  )
                ))}
              <MarkerContent className={step.thinking ? 'shimmer' : undefined}>
                {step.label}
              </MarkerContent>
            </Marker>
          </div>
        )
      })}
    </div>
  )
}
