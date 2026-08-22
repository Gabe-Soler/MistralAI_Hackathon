import {
  ArrowsClockwise,
  FolderOpen,
  Play,
  Warning,
  WaveSine,
} from '@phosphor-icons/react'
import { CatMarkerIcon } from '@/components/CatFigure'
import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker'
import { stagger } from '@/lib/stagger'
import { THINKING_STEPS, type StepIcon } from '@/lib/thinkingSteps'

const ICONS: Record<StepIcon, typeof FolderOpen> = {
  files: FolderOpen,
  run: Play,
  retry: ArrowsClockwise,
  trace: WaveSine,
  found: Warning,
}

export function ThinkingTranscript() {
  return (
    <div className="flex flex-col gap-3">
      {THINKING_STEPS.map((step, index) => {
        const Icon = step.icon ? ICONS[step.icon] : null
        return (
          <div key={step.id} style={stagger(index, '--steps-in', THINKING_STEPS.length)}>
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
