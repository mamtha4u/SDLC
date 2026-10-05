import { useEffect, useRef, useState } from "react";
import type { OrkEvent } from "./api";

const FIRST_DATA_MS = 25_000; // the server pings every 15 s; nothing by now means a proxy is holding the stream
const STALE_MS = 45_000;

/** Subscribes to the SSE stream (per project, or the user-wide stream when projectId is omitted).
 *  "connected" only turns true once data actually arrives: a corporate proxy can accept the stream and then hold it
 *  forever. Such a silent stream is closed, so it stops occupying one of the browser's few connections to this
 *  site, and the pages' polling fallback takes over. EventSource replays missed events via Last-Event-ID. */
export function useEvents(onEvent: (e: OrkEvent) => void, projectId?: string) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const url = projectId ? `/api/events/stream?project_id=${projectId}` : "/api/events/stream";
    const es = new EventSource(url, { withCredentials: true });
    let last = 0;
    const alive = () => { last = Date.now(); setConnected(true); };
    es.addEventListener("ping", alive);
    es.onmessage = (msg) => {
      alive();
      try { handler.current(JSON.parse(msg.data)); } catch { /* ignore malformed */ }
    };
    es.onerror = () => setConnected(false);
    const started = Date.now();
    const watchdog = window.setInterval(() => {
      const silent = last ? Date.now() - last > STALE_MS : Date.now() - started > FIRST_DATA_MS;
      if (silent) {
        es.close();
        setConnected(false);
        window.clearInterval(watchdog);
      }
    }, 5_000);
    return () => { window.clearInterval(watchdog); es.close(); };
  }, [projectId]);

  return connected;
}
