import { QaIssueCard } from '@/components/QaIssueCard'
import { QA_ISSUES } from '@/lib/qaIssues'

export function QaResults() {
  return (
    <section
      aria-label="QA findings"
      // One finding per row, with room to breathe between them.
      className="flex flex-col gap-12"
    >
      {QA_ISSUES.map((issue, index) => (
        <QaIssueCard key={issue.id} issue={issue} index={index} />
      ))}
    </section>
  )
}
