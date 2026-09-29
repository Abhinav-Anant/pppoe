import { useState } from "react";
import { Link } from "react-router-dom";
import type { Page, Session } from "../api/types";
import { useCan, useLive } from "../components/context";
import { Card, CheckList, Empty, fmtBytes, fmtKbit, fmtMbps, fmtNum, PageHeader, Stat, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

export default function Dashboard() {
  const { live, checks } = useLive();
  const can = useCan();
  const [top, setTop] = useState(10);
  const [by, setBy] = useState<"download_mbps" | "upload_mbps" | "peak_download_mbps">("download_mbps");
  const topUsers = useApi<Page<Session>>(can("view_sessions") ? `/api/sessions?sort=${by}&desc=true&page_size=${top}&state=active` : null, 5000);
  const plans = useApi<{ plans: Record<string, number> }>("/api/qos/status", 15000);

  if (!live) return <Empty>Waiting for the first metrics update…</Empty>;
  const s = live.sessions;
  const up = live.nics[live.uplink];
  const mem = live.host.mem_total_bytes && live.host.mem_available_bytes != null
    ? 100 * (1 - live.host.mem_available_bytes / live.host.mem_total_bytes) : null;
  const ct = live.conntrack.count != null && live.conntrack.max ? (100 * live.conntrack.count) / live.conntrack.max : null;
  const pct = (v: number | null, warn: number, bad: number) => (v == null ? undefined : v >= bad ? "bad" : v >= warn ? "warn" : "ok");

  return (
    <>
      <PageHeader title={`Dashboard — ${live.node}`}>
        <span className="text-[11px] text-zinc-500">accel-ppp up {live.accel.uptime ?? "—"}</span>
      </PageHeader>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
        <Stat label="ACCEL-PPP" value={live.accel_active ? "running" : "STOPPED"} tone={live.accel_active ? "ok" : "bad"} sub={live.accel_error ?? `cpu ${live.accel.cpu ?? "—"}`} />
        <Stat label="Active sessions" value={fmtNum(s.active)} sub={`${s.total - s.active} starting/finishing`} />
        <Stat label="Logins / min" value={fmtNum(s.logins_per_min)} sub={`logouts ${fmtNum(s.logouts_per_min)}`} tone={s.logouts_per_min > 50 ? "warn" : undefined} />
        <Stat label="Subscriber down" value={fmtMbps(s.download_mbps)} sub={`up ${fmtMbps(s.upload_mbps)}`} />
        <Stat label={`Uplink ${live.uplink}`} value={fmtMbps(up?.rx_mbps)} sub={`rx · tx ${fmtMbps(up?.tx_mbps)}`} />
        <Stat label="Uplink pps" value={fmtNum(up?.rx_pps)} sub={`rx · tx ${fmtNum(up?.tx_pps)}`} />
        <Stat label="CPU" value={live.host.cpu_percent == null ? "—" : `${live.host.cpu_percent}%`} tone={pct(live.host.cpu_percent, 70, 90)}
          sub={`softirq ${live.host.softirq_percent ?? "—"}% · load ${live.host.load?.[0] ?? "—"}`} />
        <Stat label="RAM" value={mem == null ? "—" : `${mem.toFixed(0)}%`} tone={pct(mem, 80, 92)} sub={fmtBytes(live.host.mem_total_bytes)} />
        <Stat label="Conntrack" value={ct == null ? "—" : `${ct.toFixed(1)}%`} tone={pct(ct, 70, 90)} sub={`${fmtNum(live.conntrack.count)} / ${fmtNum(live.conntrack.max)}`} />
        <Stat label="NIC errors / drops" value={`${fmtNum(up?.errors)} / ${fmtNum(up?.drops)}`} tone={up && (up.errors || up.drops) ? "warn" : "ok"} sub={live.uplink} />
        <Stat label="Duplicates" value={fmtNum(s.duplicates)} tone={s.duplicates ? "warn" : "ok"} sub="same user or MAC twice" />
        <Stat label="Unshaped" value={fmtNum(s.unshaped)} tone={s.unshaped ? "warn" : "ok"} sub="active, no RADIUS rate" />
        <Stat label="PPPoE PADI" value={fmtNum(Number(live.accel.pppoe?.["recv PADI"] ?? NaN))} sub={`dropped ${live.accel.pppoe?.["drop PADI"] ?? "—"}`} />
        <Stat label="NAT pools" value={fmtNum(live.nat.length)} sub={`${fmtNum(live.nat.reduce((a, n) => a + n.packets, 0))} flows translated`} />
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-3">
        <Card title="Health" className="xl:col-span-1">
          {checks ? <CheckList checks={checks} /> : <Empty>Running checks…</Empty>}
        </Card>

        {can("view_sessions") && (
          <Card
            title="Top subscribers"
            className="xl:col-span-2"
            actions={
              <>
                <select className="rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 text-[11px]" value={by} onChange={(e) => setBy(e.target.value as typeof by)}>
                  <option value="download_mbps">current download</option>
                  <option value="upload_mbps">current upload</option>
                  <option value="peak_download_mbps">peak download</option>
                </select>
                <select className="rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 text-[11px]" value={top} onChange={(e) => setTop(Number(e.target.value))}>
                  {[10, 50, 100].map((n) => <option key={n} value={n}>Top {n}</option>)}
                </select>
              </>
            }
          >
            <div className="max-h-96 overflow-auto">
              <table className={T.table}>
                <thead>
                  <tr>{["User", "IP", "Plan", "Down", "Up", "Peak down", "Total down"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {topUsers.data?.items.map((r) => (
                    <tr key={r.sid} className={T.tr}>
                      <td className={T.td}><Link className="text-sky-400 hover:underline" to={`/sessions/${r.sid}`}>{r.username}</Link></td>
                      <td className={`${T.td} num`}>{r.ip ?? "—"}</td>
                      <td className={T.td}>{fmtKbit(r.rate_down_kbit)}/{fmtKbit(r.rate_up_kbit)}</td>
                      <td className={`${T.td} num`}>{fmtMbps(r.download_mbps)}</td>
                      <td className={`${T.td} num`}>{fmtMbps(r.upload_mbps)}</td>
                      <td className={`${T.td} num`}>{fmtMbps(r.peak_download_mbps)}</td>
                      <td className={`${T.td} num`}>{fmtBytes(r.download_bytes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {topUsers.data?.items.length === 0 && <Empty>No active sessions</Empty>}
            </div>
          </Card>
        )}

        <Card title="Active sessions by plan (RADIUS rate)">
          {plans.data && Object.keys(plans.data.plans).length ? (
            <table className={T.table}>
              <tbody>
                {Object.entries(plans.data.plans).sort((a, b) => b[1] - a[1]).map(([rate, n]) => (
                  <tr key={rate} className={T.tr}>
                    <td className={T.td}>{rate === "unshaped" ? <span className="text-amber-400">unshaped</span> : rate.split("/").map((k) => fmtKbit(Number(k))).join(" / ")}</td>
                    <td className={`${T.td} num text-right`}>{fmtNum(n)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No active sessions</Empty>}
        </Card>
      </div>
    </>
  );
}
