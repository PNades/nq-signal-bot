// The server's status (GET /api/status, then server-sent events on /api/events every ~2 s; src/nqbot/ui/status.py).
// If the stream drops, the panel marks everything stale and says kill/flat from this page can't reach the server.
import { useEffect, useRef, useState } from "react";
import { getJson } from "@/lib/api";

export type EngineState = "offline" | "live" | "paused" | "killed";
export interface ServerAlert { id: string; kind: "crit" | "warn" | "info"; title: string; text: string }
export interface AccountBrief { name: string; firm: string; phase: string; draft: boolean; demo: boolean; preset?: string | null }
export interface ServerStatus {
  server_time_utc: string;
  host: string;
  synthetic: boolean;
  ssd: { found: boolean; message: string };
  engine: { state: EngineState; here: boolean; note: string };
  /** true while the live engine runs on this machine during market hours: Effects Auto turns Calm */
  calm_forced: boolean;
  alerts: ServerAlert[];
  accounts: AccountBrief[];
}

export function useServerStatus() {
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [connected, setConnected] = useState(true);
  const lastBeat = useRef(Date.now());
  useEffect(() => {
    let es: EventSource | null = null;
    let stopped = false;
    getJson<ServerStatus>("/api/status").then((s) => { if (!stopped) { setStatus(s); lastBeat.current = Date.now(); } }).catch(() => setConnected(false));
    const open = () => {
      es = new EventSource("/api/events");
      es.addEventListener("status", (ev) => {
        try { setStatus(JSON.parse((ev as MessageEvent).data)); lastBeat.current = Date.now(); setConnected(true); } catch { /* ignore a bad frame */ }
      });
      es.onerror = () => { /* EventSource reconnects by itself; the watchdog below decides "lost" */ };
    };
    open();
    // lost = no frame for 8 s (frames come every ~2 s)
    const watch = setInterval(() => setConnected(Date.now() - lastBeat.current < 8000), 1000);
    return () => { stopped = true; es?.close(); clearInterval(watch); };
  }, []);
  return { status, connected };
}
