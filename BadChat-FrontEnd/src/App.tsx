import { Landing } from "@/pages/Landing";
import { RunPage } from "@/pages/RunPage";
import { runIdFromPath, useRoute } from "@/hooks/useRoute";

/**
 * Two routes. `/` is the landing page, scroll-choreographed against placeholder data;
 * `/<run_id>` is a live run, same design, driven by the event stream instead of scroll.
 *
 * The server serves index.html for any unmatched path (see server.py), so a deep link to
 * a run works on a cold load.
 */
function App() {
  const runId = runIdFromPath(useRoute());
  return runId ? <RunPage runId={runId} /> : <Landing />;
}

export default App;
