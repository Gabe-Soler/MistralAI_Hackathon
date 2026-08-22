import { API_BASE, runUrl, runsUrl } from "./base";
import type { RunEvent, RunState, RunSummary } from "./events";

/**
 * Talking to the engine.
 *
 * The stream uses the browser's own EventSource rather than a fetch reader, for two
 * concrete reasons. It reconnects on its own and resends `Last-Event-ID`, which is the
 * exact header `Bus.subscribe(since=n)` was built around -- a fetch client would have to
 * reimplement the one feature the server was designed for. And `tsconfig` omits
 * `DOM.Iterable`, so `for await (const chunk of res.body)` does not even typecheck; the
 * alternative is a hand-rolled frame splitter that also has to skip the `: ping` comment
 * lines sse-starlette emits every 15s.
 */

export interface StreamHandle {
  close(): void;
}

/**
 * Subscribe to a run. `onEvent` receives the bus sequence alongside the payload -- the
 * events carry no timestamps, so `seq` is the only ordering the client gets.
 *
 * Never pass credentials: the engine sends `Access-Control-Allow-Origin: *` without
 * `allow_credentials`, and browsers reject that combination outright.
 */
export function openRunStream(
  runId: string,
  onEvent: (seq: number, ev: RunEvent) => void,
  onStatus?: (connected: boolean) => void,
): StreamHandle {
  const es = new EventSource(runUrl(runId, "stream"));

  es.onopen = () => onStatus?.(true);
  es.onerror = () => onStatus?.(false); // EventSource retries by itself; do not close here

  // The engine deliberately leaves `event:` unset so everything arrives as `message`.
  es.onmessage = (m: MessageEvent<string>) => {
    let parsed: RunEvent;
    try {
      parsed = JSON.parse(m.data) as RunEvent;
    } catch {
      return; // a malformed frame must not kill the stream
    }
    onEvent(Number(m.lastEventId), parsed);
  };

  return { close: () => es.close() };
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${url}`);
  return (await res.json()) as T;
}

/**
 * The snapshot, fetched once for what the stream does not carry: tenant and persona
 * names, invariant text, the target URL. Findings and phase come from events instead --
 * the stream always replays from seq 1, so it is already complete, and deriving them from
 * one source removes the snapshot-versus-stream race rather than papering over it.
 */
export const fetchRunState = (runId: string, signal?: AbortSignal): Promise<RunState> =>
  getJson<RunState>(runUrl(runId, "state"), signal);

export const fetchRuns = (signal?: AbortSignal): Promise<RunSummary[]> =>
  getJson<RunSummary[]>(runsUrl(), signal);

export const shotUrl = (runId: string, name: string): string =>
  runUrl(runId, `shots/${encodeURIComponent(name)}`);

export async function answerQuestion(
  runId: string,
  questionId: string,
  answer: string,
): Promise<void> {
  const res = await fetch(runUrl(runId, "answer"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_id: questionId, answer }),
  });
  if (!res.ok) throw new Error(`answer failed: ${res.status}`);
}

export { API_BASE };
