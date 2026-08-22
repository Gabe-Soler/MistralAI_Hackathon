import { QA_ISSUES } from '@/lib/qaIssues'

export type StepIcon = 'files' | 'run' | 'retry' | 'trace' | 'found'

export interface ThinkingStep {
  id: string
  /** Which Marker variant renders this line. */
  variant?: 'default' | 'border' | 'separator'
  /** Active work: shows the cat instead of an icon, and shimmers. */
  thinking?: boolean
  icon?: StepIcon
  label: string
}

const critical = QA_ISSUES.filter((i) => i.severity === 'critical').length

/**
 * The run log. Placeholder until the QA backend streams real steps — the
 * transcript is driven entirely off this array, so swapping it for live events
 * needs no change to the components.
 */
export const THINKING_STEPS: ThinkingStep[] = [
  { id: 's0', variant: 'separator', label: 'run 4f2a91 · gpt-oss vs mistral-large' },
  { id: 's1', thinking: true, label: 'Reading the app under test…' },
  { id: 's2', icon: 'files', label: 'Explored 41 files across 6 routes' },
  { id: 's3', icon: 'trace', label: 'Collected 2,140 conversation traces' },
  { id: 's4', thinking: true, label: 'Replaying failed traces…' },
  { id: 's5', icon: 'run', label: 'Ran 128 cases across 4 models' },
  { id: 's6', icon: 'retry', label: 'Re-ran 12 flaky cases at temperature 0' },
  {
    id: 's7',
    icon: 'found',
    label: `Found ${QA_ISSUES.length} issues — ${critical} critical`,
  },
  { id: 's8', variant: 'separator', label: 'Findings' },
]
