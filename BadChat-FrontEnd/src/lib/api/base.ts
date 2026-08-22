/**
 * Where the engine lives, from the browser's point of view.
 *
 * Empty by default, which means same-origin relative URLs: in production the engine
 * serves this bundle itself, and in dev vite.config.ts proxies /api to it. Set
 * VITE_API_BASE to point straight at the engine instead -- the escape hatch if the dev
 * proxy ever buffers the event stream. CORS is `allow_origins=["*"]` with credentials
 * unset, so a cross-origin EventSource works, but it must never send credentials:
 * `*` and credentials together are rejected by every browser.
 */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export const runUrl = (runId: string, path: string): string =>
  `${API_BASE}/api/${encodeURIComponent(runId)}/${path}`;

export const runsUrl = (): string => `${API_BASE}/api/runs`;
