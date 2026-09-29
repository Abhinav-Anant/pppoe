import { createContext, useContext, useRef, useState, type ReactNode } from "react";
import type { Check, Live, Me } from "../api/types";
import { useSocket } from "../websocket/useSocket";

// ---- auth --------------------------------------------------------------------
export const AuthContext = createContext<{ me: Me; logout: () => void }>(null!);
export const useAuth = () => useContext(AuthContext);
export const useCan = () => {
  const { me } = useAuth();
  return (perm: string) => me.permissions.includes(perm);
};

// ---- live metrics --------------------------------------------------------------
// One /api/ws/metrics socket for the whole app; the last 15 minutes are kept in the
// browser for the monitoring graphs (longer history belongs in Prometheus/Zabbix).
export interface Point {
  t: number; down: number; up: number; rxpps: number; txpps: number; sessions: number;
  cpu: number | null; softirq: number | null; conntrack: number | null;
}
const WINDOW_S = 15 * 60;

interface LiveState { live: Live | null; history: Point[]; connected: boolean; checks: Check[] | null }
const LiveContext = createContext<LiveState>({ live: null, history: [], connected: false, checks: null });
export const useLive = () => useContext(LiveContext);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [live, setLive] = useState<Live | null>(null);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const history = useRef<Point[]>([]);

  const { connected } = useSocket<Live>("/api/ws/metrics", (m) => {
    const up = m.nics[m.uplink];
    history.current = [
      ...history.current.filter((p) => p.t > m.timestamp - WINDOW_S),
      {
        t: m.timestamp, down: m.sessions.download_mbps, up: m.sessions.upload_mbps,
        rxpps: up?.rx_pps ?? 0, txpps: up?.tx_pps ?? 0, sessions: m.sessions.active,
        cpu: m.host.cpu_percent, softirq: m.host.softirq_percent, conntrack: m.conntrack.count,
      },
    ];
    setLive(m);
  });
  useSocket<{ checks: Check[] }>("/api/ws/system", (m) => setChecks(m.checks));

  return (
    <LiveContext.Provider value={{ live, history: history.current, connected, checks }}>{children}</LiveContext.Provider>
  );
}
