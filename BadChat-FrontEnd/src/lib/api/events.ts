/**
 * The engine's SSE wire format, mirrored in TypeScript.
 *
 * Hand-written rather than generated, and kept honest from the Python side:
 * `engine/tests/test_wire_contract.py` snapshots `TypeAdapter(Event).json_schema()`, so
 * changing an event fails a test that names this file. A generator was the alternative
 * and loses on two counts here -- it derives optionality from JSON Schema `required`,
 * which would mark every defaulted field `?` even though the wire always carries it, and
 * `erasableSyntaxOnly` in tsconfig forbids the `enum`s it likes to emit.
 *
 * Two properties of the wire that shape everything below:
 *
 *   1. Every field is always present. Pydantic dumps with no `exclude_none`, so defaults
 *      are serialized too. Fields are therefore `T | null`, never `T | undefined` -- you
 *      check for null, and you never have to guard a missing key.
 *   2. Events are tagged with `type` and framed as the default `message` SSE event, so
 *      one `es.onmessage` handler receives all eight and narrows on `type`.
 */

/** Which interface an actor used. `voice` is declared by the engine but was cut. */
export type Channel = "api" | "chat" | "web" | "voice";

/**
 * The oracle's judgement of one step.
 *
 * `error` means the step itself failed -- an adapter crash or a dead target -- and the
 * engine is deliberate that it must never read as a pass. It is not a finding about the
 * product either: `--ci` and the CLI summary both count only `breach`.
 *
 * `suspected` is an LLM judgement against a stated invariant, for the bug classes a canary
 * scan is blind to. It is NOT the same claim as `breach`: one is a lookup against data the
 * tool planted, the other is inference and can be wrong. Render it as a weaker claim and
 * never fold it into the breach count.
 */
export type Verdict = "benign" | "breach" | "error" | "suspected";

/** A rule the app is supposed to enforce. `cite` is `file:line` in the target's source. */
export interface Invariant {
  id: string;
  name: string;
  rule: string;
  /** `code` was inferred by reading the repo; `dev` was confirmed by a human. */
  source: string;
  cite: string | null;
}

export interface Step {
  id: string;
  persona_id: string;
  channel: Channel;
  action: string;
  target_ref: string | null;
}

export interface Finding {
  id: string;
  play_id: string;
  persona_id: string;
  channel: Channel;
  action: string;
  verdict: Verdict;
  invariant_id: string | null;
  cite: string | null;
  /** Redacted excerpt of what came back. The evidence, and what the copy button yields. */
  evidence: string;
  /** Why the judge thinks a rule was broken. Empty on canary breaches. */
  rationale: string;
  /** Declared by the engine but never populated -- always `[]`. Do not build UI for it. */
  repro: Step[];
}

// ---------------------------------------------------------------------------
// The eight events
// ---------------------------------------------------------------------------

export interface QuestionEvent {
  type: "question";
  id: string;
  text: string;
  options: string[];
}

export interface SeedEvent {
  type: "seed";
  tenant_id: string;
  persona_id: string | null;
  detail: string;
  artifact_id: string | null;
  /** False when the signup, artifact or whole tenant failed. Colour from this, not `detail`. */
  ok: boolean;
}

export interface TruthUpdatedEvent {
  type: "truth_updated";
  invariant: Invariant;
}

export interface PhaseEvent {
  type: "phase";
  /**
   * Deliberately `string`, not a union of PHASES. The engine types it as a bare `str`,
   * and `"failed"` was added after the first clients were written -- a literal union
   * would have thrown mid-run the first time a seeding failure landed. Narrow with
   * `isPhase()` for display and always keep a default branch.
   */
  phase: string;
  /** Why, on `failed`. Empty for healthy phases. */
  detail: string;
}

export interface StepStartedEvent {
  type: "step_started";
  play_id: string;
  persona_id: string;
  channel: Channel;
  action: string;
}

export interface StepFinishedEvent {
  type: "step_finished";
  play_id: string;
  persona_id: string;
  channel: Channel;
  action: string;
  /** Redacted head window of the response body. */
  detail: string;
  verdict: Verdict;
  invariant_id: string | null;
  /** Basename of a Browser Use frame; fetch it from `/api/{run_id}/shots/{shot}`. */
  shot: string | null;
}

export interface FindingEvent {
  type: "finding";
  finding: Finding;
}

export interface ChainEvent {
  type: "chain";
  play_id: string;
  title: string;
  steps: StepFinishedEvent[];
  verdict: Verdict;
  /**
   * The prize find: every step looked fine alone, but together they broke in. True only
   * when the control run -- the final step replayed by a persona that took no part in the
   * setup -- came back benign. `control_verdict` is that run's result.
   */
  compound: boolean;
  control_verdict: Verdict | null;
}

/** Everything `es.onmessage` can deliver. Narrow on `type`. */
export type RunEvent =
  | QuestionEvent
  | SeedEvent
  | TruthUpdatedEvent
  | PhaseEvent
  | StepStartedEvent
  | StepFinishedEvent
  | FindingEvent
  | ChainEvent;

// ---------------------------------------------------------------------------
// Phases
// ---------------------------------------------------------------------------

export const PHASES = ["reading", "seeding", "attacking", "done", "failed"] as const;
export type Phase = (typeof PHASES)[number];

export const isPhase = (p: string): p is Phase => (PHASES as readonly string[]).includes(p);

/**
 * A run that ended without proving anything -- seeding could not create two tenants, or
 * no step ever reached the target. Distinct from `done`, and never to be rendered as a
 * clean result: the engine exits 2 for it.
 */
export const isFailed = (phase: string): boolean => phase === "failed";

// ---------------------------------------------------------------------------
// GET /api/{run_id}/state and GET /api/runs
// ---------------------------------------------------------------------------

export interface Tenant {
  id: string;
  kind: string;
  name: string;
  /** The planted tag. Its presence in another tenant's response is the whole detector. */
  canary: string;
}

export interface PublicPersona {
  id: string;
  tenant_id: string;
  role: string;
  name: string;
  email: string;
  /** The id the TARGET app assigned, if it exposed one. */
  ref: string | null;
}

export interface Artifact {
  id: string;
  tenant_id: string;
  owner_persona_id: string;
  kind: string;
  ref: string;
  title: string;
  body: string;
  /** Decimal on the wire: a JSON *string*, not a number. Parse before arithmetic. */
  amount: string | null;
}

/** Credentials are dropped server-side -- this is never the full Manifest. */
export interface PublicManifest {
  tenants: Tenant[];
  personas: PublicPersona[];
  artifacts: Artifact[];
}

export interface GroundTruth {
  product_name: string;
  product_type: string;
  domain: string;
  roles: string[];
  invariants: Invariant[];
  endpoints: string[];
  signup_hint: string;
  endpoint_bodies: Record<string, string[]>;
}

export interface SessionConfig {
  run_id: string;
  repo: string;
  target: string;
  channels: Channel[];
  support_phone: string | null;
  replay: string | null;
  ci: boolean;
  headless: boolean;
  rps: number;
}

export interface RunState {
  config: SessionConfig | null;
  ground_truth: GroundTruth | null;
  manifest: PublicManifest;
  findings: Finding[];
  phase: string;
  phase_detail: string;
}

export interface RunSummary {
  run_id: string;
  /** ISO 8601, derived from `run_id`. Empty when the id is not a timestamp. */
  started_at: string;
  target: string;
  phase: string;
  phase_detail: string;
  findings: number;
  breaches: number;
  errors: number;
  /** True when this process is running it; false for a directory on disk. */
  live: boolean;
}
