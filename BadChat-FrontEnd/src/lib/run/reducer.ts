import type {
  ChainEvent,
  Finding,
  Invariant,
  PublicManifest,
  RunEvent,
  RunState,
  SeedEvent,
  Verdict,
} from "@/lib/api/events";

/**
 * Folding the event stream into something renderable.
 *
 * Everything here is derived from events, never from the /state snapshot -- the stream
 * replays from seq 1 on every connect, so it is always complete. The snapshot supplies
 * only the reference data events do not carry (names, invariant text) via `hydrate`.
 */

export interface LaneStep {
  key: string;
  playId: string;
  personaId: string;
  channel: string;
  action: string;
  /** `running` until the matching step_finished lands. */
  status: "running" | Verdict;
  detail: string;
  shot: string | null;
}

export interface Lane {
  playId: string;
  steps: LaneStep[];
}

export interface RunView {
  runId: string;
  phase: string;
  phaseDetail: string;
  connected: boolean;

  invariants: Invariant[];
  seeds: SeedEvent[];
  lanes: Lane[];
  findings: Finding[];
  /** Keyed by play_id. Arrives AFTER the findings it re-grades -- see toIssue. */
  chains: Record<string, ChainEvent>;
  question: { id: string; text: string; options: string[] } | null;

  /** Reference data from /state: names, invariant text, the target. Not derived. */
  manifest: PublicManifest | null;
  target: string;

  lastSeq: number;
  /** Non-contiguous seq means the bus dropped a frame for this subscriber. */
  gaps: number;
}

export const initialRunView = (runId: string): RunView => ({
  runId,
  phase: "reading",
  phaseDetail: "",
  connected: false,
  invariants: [],
  seeds: [],
  lanes: [],
  findings: [],
  chains: {},
  question: null,
  manifest: null,
  target: "",
  lastSeq: 0,
  gaps: 0,
});

export type RunAction =
  | { kind: "event"; seq: number; ev: RunEvent }
  | { kind: "status"; connected: boolean }
  | { kind: "hydrate"; state: RunState }
  | { kind: "reset"; runId: string };

/**
 * A step is identified by what it is, not by an id: step_started and step_finished carry
 * no shared key. persona_id has to be part of it -- the compound chain's control run
 * replays an identical action under a different persona, and folding those together would
 * erase the very comparison the chain exists to make.
 */
const stepKey = (e: { play_id: string; persona_id: string; channel: string; action: string }) =>
  `${e.play_id}|${e.persona_id}|${e.channel}|${e.action}`;

function withLane(lanes: Lane[], playId: string, fn: (l: Lane) => Lane): Lane[] {
  const i = lanes.findIndex((l) => l.playId === playId);
  if (i === -1) return [...lanes, fn({ playId, steps: [] })];
  const next = [...lanes];
  next[i] = fn(next[i]);
  return next;
}

function applyEvent(s: RunView, ev: RunEvent): RunView {
  switch (ev.type) {
    case "phase":
      return { ...s, phase: ev.phase, phaseDetail: ev.detail };

    case "truth_updated":
      return s.invariants.some((i) => i.id === ev.invariant.id)
        ? s
        : { ...s, invariants: [...s.invariants, ev.invariant] };

    case "question":
      return { ...s, question: { id: ev.id, text: ev.text, options: ev.options } };

    case "seed":
      return { ...s, seeds: [...s.seeds, ev] };

    case "step_started":
      return {
        ...s,
        lanes: withLane(s.lanes, ev.play_id, (l) => ({
          ...l,
          steps: [
            ...l.steps,
            {
              key: stepKey(ev),
              playId: ev.play_id,
              personaId: ev.persona_id,
              channel: ev.channel,
              action: ev.action,
              status: "running",
              detail: "",
              shot: null,
            },
          ],
        })),
      };

    case "step_finished": {
      const key = stepKey(ev);
      return {
        ...s,
        lanes: withLane(s.lanes, ev.play_id, (l) => {
          // Pair with the LAST still-running step of the same identity; a finish with no
          // start is appended rather than dropped, so a lost start costs a row's spinner,
          // never the row itself.
          const i = l.steps.map((x) => x.key === key && x.status === "running").lastIndexOf(true);
          const filled: LaneStep = {
            key,
            playId: ev.play_id,
            personaId: ev.persona_id,
            channel: ev.channel,
            action: ev.action,
            status: ev.verdict,
            detail: ev.detail,
            shot: ev.shot,
          };
          if (i === -1) return { ...l, steps: [...l.steps, filled] };
          const steps = [...l.steps];
          steps[i] = filled;
          return { ...l, steps };
        }),
      };
    }

    case "finding":
      return s.findings.some((f) => f.id === ev.finding.id)
        ? s
        : { ...s, findings: [...s.findings, ev.finding] };

    case "chain":
      return { ...s, chains: { ...s.chains, [ev.play_id]: ev } };

    default:
      // An event type this client does not know yet. Ignoring it is correct: the union is
      // versioned by the engine and a new member must never break an older dashboard.
      return s;
  }
}

export function runReducer(s: RunView, a: RunAction): RunView {
  switch (a.kind) {
    case "reset":
      return initialRunView(a.runId);

    case "status":
      return { ...s, connected: a.connected };

    case "hydrate":
      return {
        ...s,
        manifest: a.state.manifest,
        target: a.state.config?.target ?? "",
        invariants: a.state.ground_truth?.invariants.length
          ? a.state.ground_truth.invariants
          : s.invariants,
      };

    case "event": {
      // Sequence gate. StrictMode double-mounts the effect and a reconnect without a
      // Last-Event-ID replays the whole log, so the same event can arrive twice; applying
      // it twice would duplicate findings and seed rows.
      if (a.seq <= s.lastSeq) return s;
      const gap = s.lastSeq > 0 && a.seq > s.lastSeq + 1 ? 1 : 0;
      return { ...applyEvent(s, a.ev), lastSeq: a.seq, gaps: s.gaps + gap };
    }

    default:
      return s;
  }
}
