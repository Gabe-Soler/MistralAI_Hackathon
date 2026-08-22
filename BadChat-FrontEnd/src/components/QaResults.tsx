import { QaIssueCard } from '@/components/QaIssueCard'
import { QA_ISSUES, type QaIssue } from '@/lib/qaIssues'
import type { RevealMode } from '@/lib/reveal'

interface QaResultsProps {
  /** Defaults to the placeholder findings, so the landing page is unchanged. */
  issues?: QaIssue[]
  mode?: RevealMode
}

export function QaResults({ issues = QA_ISSUES, mode = 'scroll' }: QaResultsProps) {
  return (
    <section
      aria-label="QA findings"
      // One finding per row, with room to breathe between them.
      className="flex flex-col gap-12"
    >
      {issues.map((issue, index) => (
        <QaIssueCard
          key={issue.id}
          issue={issue}
          index={index}
          count={issues.length}
          mode={mode}
        />
      ))}
    </section>
  )
}
