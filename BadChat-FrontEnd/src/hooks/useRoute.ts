import { useEffect, useState } from "react";

/**
 * Two routes, one segment: `/` is the landing page and `/<run_id>` is a run.
 *
 * Hand-rolled rather than a router dependency -- there is no nesting, no loader, no
 * search state, and the server already serves index.html for any unmatched path, so deep
 * links work without history-fallback config. `navigate` dispatches its own popstate
 * because pushState deliberately does not fire one.
 */
export function useRoute(): string {
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  return path;
}

export function navigate(to: string): void {
  window.history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** The run id in the URL, or null on the landing page. */
export function runIdFromPath(path: string): string | null {
  const seg = path.replace(/^\/+|\/+$/g, "").split("/")[0];
  return seg ? decodeURIComponent(seg) : null;
}
