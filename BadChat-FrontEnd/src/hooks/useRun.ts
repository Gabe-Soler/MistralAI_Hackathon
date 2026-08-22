import { useEffect, useReducer } from "react";

import { fetchRunState, openRunStream } from "@/lib/api/client";
import { initialRunView, runReducer, type RunView } from "@/lib/run/reducer";

/**
 * Everything a live run needs, folded from its event stream.
 *
 * State is component-local on purpose. StrictMode double-invokes the effect (connect ->
 * cleanup -> connect) and the reconnect replays from seq 1; a module-level store would
 * apply every replayed event on top of the first pass. Local state plus the reducer's
 * sequence gate makes both harmless.
 */
export function useRun(runId: string): RunView {
  const [view, dispatch] = useReducer(runReducer, runId, initialRunView);

  useEffect(() => {
    dispatch({ kind: "reset", runId });

    const ac = new AbortController();
    // The snapshot is only for what events do not carry: persona and tenant names, and
    // the invariant text a finding cites. Findings and phase come from the stream.
    fetchRunState(runId, ac.signal)
      .then((state) => dispatch({ kind: "hydrate", state }))
      .catch(() => {}); // a missing snapshot degrades the labels, not the run

    const stream = openRunStream(
      runId,
      (seq, ev) => dispatch({ kind: "event", seq, ev }),
      (connected) => dispatch({ kind: "status", connected }),
    );

    return () => {
      ac.abort();
      stream.close();
    };
  }, [runId]);

  return view;
}
