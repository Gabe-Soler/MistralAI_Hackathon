import type { Finding } from "@/lib/api/events";
import type { QaIssue, QaSeverity } from "@/lib/qaIssues";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import type { RunView } from "./reducer";

/**
 * Engine model -> UI model.
 *
 * The two do not line up: the engine has verdicts and invariants, the cards want a
 * severity and a summary. Everything opinionated about that mapping lives here.
 */

/**
 * Findings shown to the user are BREACHES ONLY.
 *
 * `Verdict.error` findings exist and are common -- an adapter crash or a dead target --
 * but they are not defects in the product under test. The engine already treats them that
 * way: the CLI summary and the --ci exit code both count only breaches. And an error
 * Finding carries no invariant_id and no cite, so it cannot fill the should-vs-did panel
 * that is the card's entire purpose; it would render as a card asserting a severity
 * nobody measured. Errors surface in the transcript and the run-health line instead.
 */
export const isReportable = (f: Finding): boolean =>
  f.verdict === "breach" || f.verdict === "suspected";

function severityOf(f: Finding, compoundPlays: Set<string>): QaSeverity {
  // Judged, not proven. Its own tier, below every proven one, however confident the
  // model sounded -- `breach` means we planted the data and found it where it could not
  // legally be, and nothing inferred earns that rung.
  if (f.verdict === "suspected") return "suspected";
  if (compoundPlays.has(f.play_id)) return "critical";
  return f.invariant_id ? "major" : "minor";
}

/**
 * Severity is computed at render, never frozen when the finding arrives.
 *
 * FindingEvent is emitted inside run_step; the ChainEvent that proves `compound` is
 * emitted by run_play only after the control run finishes. A compound breach therefore
 * arrives as `major` and becomes `critical` seconds later. Storing it at insert time
 * would leave the best finding in the run permanently under-graded.
 */
export function toIssues(view: RunView): QaIssue[] {
  const compound = new Set(
    Object.values(view.chains).filter((c) => c.compound).map((c) => c.play_id),
  );
  const rules = new Map(view.invariants.map((i) => [i.id, i]));
  const personas = new Map((view.manifest?.personas ?? []).map((p) => [p.id, p.name]));

  return view.findings.filter(isReportable).map((f) => {
    const rule = f.invariant_id ? rules.get(f.invariant_id) : undefined;
    const who = personas.get(f.persona_id) ?? f.persona_id;
    return {
      id: f.id,
      severity: severityOf(f, compound),
      summary: rule ? `${rule.name} — ${f.action}` : f.action,
      source: `${f.channel} · ${who}${f.cite ? ` · ${f.cite}` : ""}`,
      // What the copy button yields, so it must stand alone: the rule that was broken,
      // then the response that broke it.
      error: [
        rule ? `Expected: ${rule.rule}` : null,
        f.cite ? `Defined at: ${f.cite}` : null,
        f.rationale ? `Judged: ${f.rationale}` : null,
        "",
        f.evidence || "(no response body captured)",
      ]
        .filter((l) => l !== null)
        .join("\n"),
    };
  });
}

const PHASE_LABEL: Record<string, string> = {
  reading: "Reading the app under test",
  seeding: "Building a fake world inside it",
  attacking: "Using it as somebody else",
  done: "Findings",
  failed: "Run failed",
};

/**
 * The transcript. Plays run concurrently (campaign.run gathers over them), so step events
 * for different play_ids interleave; each line therefore carries its play, or the log
 * reads as two conversations spliced together.
 */
export function toThinkingSteps(view: RunView): ThinkingStep[] {
  const out: ThinkingStep[] = [];

  out.push({
    id: "phase-head",
    variant: "separator",
    label: `run ${view.runId}${view.target ? ` · ${view.target}` : ""}`,
  });

  if (view.invariants.length) {
    out.push({
      id: "rules",
      icon: "files",
      label: `${view.invariants.length} rule${view.invariants.length === 1 ? "" : "s"} read from the source`,
    });
  }

  const failedSeeds = view.seeds.filter((s) => !s.ok).length;
  if (view.seeds.length) {
    out.push({
      id: "seeded",
      icon: failedSeeds ? "found" : "run",
      label: failedSeeds
        ? `${view.seeds.length} seed steps, ${failedSeeds} failed`
        : `Seeded ${new Set(view.seeds.map((s) => s.tenant_id)).size} tenants`,
    });
  }

  for (const lane of view.lanes) {
    out.push({ id: `lane-${lane.playId}`, variant: "separator", label: lane.playId });
    for (const s of lane.steps) {
      out.push({
        id: `${lane.playId}-${s.key}`,
        thinking: s.status === "running",
        icon: s.status === "breach" ? "found" : s.status === "error" ? "retry" : "trace",
        label: `${s.channel} · ${s.action}`,
      });
    }
  }

  if (view.phase === "done" || view.phase === "failed") {
    out.push({
      id: "phase-tail",
      variant: "separator",
      label: PHASE_LABEL[view.phase] ?? view.phase,
    });
  } else {
    out.push({
      id: "phase-now",
      thinking: true,
      label: PHASE_LABEL[view.phase] ?? view.phase,
    });
  }
  return out;
}

/**
 * One line stating whether the run can be believed.
 *
 * A run that failed proves nothing, and "0 findings" next to "8 steps errored" is the
 * distinction the whole tool exists to make. This is why errors do not need a severity.
 */
export function runHealth(view: RunView): { ok: boolean; text: string } {
  if (view.phase === "failed") {
    return { ok: false, text: view.phaseDetail || "Run failed — this proves nothing." };
  }
  const steps = view.lanes.reduce((n, l) => n + l.steps.length, 0);
  const errored = view.lanes.reduce(
    (n, l) => n + l.steps.filter((s) => s.status === "error").length,
    0,
  );
  const tenants = new Set(view.seeds.map((s) => s.tenant_id)).size;
  const suspected = view.findings.filter((f) => f.verdict === "suspected").length;
  const parts = [`${steps} step${steps === 1 ? "" : "s"}`];
  if (suspected) parts.push(`${suspected} suspected`);
  if (errored) parts.push(`${errored} errored`);
  if (tenants) parts.push(`${tenants} tenants seeded`);
  return { ok: errored === 0, text: parts.join(" · ") };
}
