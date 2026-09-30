import { useState } from "react";
import { Badge, Card, Empty, ErrorNote, fmtNum, fmtTime, KV, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface Row {
  name: string; date: string; result: string; fail_reasons: string[]; sessions: number; established: number;
  setup_rate_per_s: number | null; setup_p95_ms: number | null; target_gbps: number | null;
  down_gbps: number | null; up_gbps: number | null; down_loss: number | null; up_loss: number | null;
  down_pps: number | null; cpu_avg: number | null; cpu_peak_core: number | null; mem_used_mb: number | null;
}
interface HostWin {
  cpu_avg_percent: number; cpu_per_core_percent: number[]; cpu_peak_core_percent_1s: number;
  softirq_per_core_percent: number[]; accel_cpu_percent?: number; generators_cpu_percent?: number;
  softnet_drops: number; time_squeeze: number; conntrack_max: number;
}
interface Flow { gbps: number; pps?: number; loss_percent?: number | null; failed_flows: number; host: HostWin }
interface Doc {
  name: string; host: Record<string, string | number>; accel_ppp: string; notes: string[]; result: string; fail_reasons: string[];
  sessions: Record<string, unknown> & { setup_latency_ms?: Record<string, number>; cpu_hold?: HostWin };
  traffic: { target_gbps: number; sessions_used: number; per_session_mbit: number; shaper_drops: number; down: Flow; up: Flow } | null;
  tcp_max: { down: Flow; up: Flow } | null;
}
interface Rec { key: string; current: string; recommended: string; reason: string; apply: boolean }
interface Tuning {
  diagnostics: {
    cpus: number; mem_mb: number; governor: string | null; irqbalance: string | null;
    nics: { name: string; driver: string; rx_queues: number; tx_queues: number }[];
    softnet: { cpu: number; processed: number; dropped: number; time_squeeze: number }[];
    softirqs: Record<string, number[]>; irqs: { irq: number; name: string; per_cpu: number[] }[];
  };
  recommendations: Rec[]; applied: Record<string, string>;
}

const g = (x: number | null | undefined) => (x == null ? "—" : x.toFixed(2));

function Cores({ label, values }: { label: string; values?: number[] }) {
  if (!values?.length) return null;
  return (
    <div className="flex items-end gap-1">
      <span className="w-24 shrink-0 text-[11px] text-zinc-500">{label}</span>
      {values.map((v, i) => (
        <div key={i} title={`cpu${i}: ${v}%`} className="flex h-10 w-3 items-end bg-zinc-800">
          <div className={v > 90 ? "w-full bg-red-500" : v > 60 ? "w-full bg-amber-400" : "w-full bg-emerald-500"} style={{ height: `${Math.min(v, 100)}%` }} />
        </div>
      ))}
    </div>
  );
}

function Detail({ name }: { name: string }) {
  const { data: d, error } = useApi<Doc>(`/api/benchmarks/${name}`);
  if (error) return <ErrorNote error={error} />;
  if (!d) return <Empty>Loading…</Empty>;
  const s = d.sessions, t = d.traffic;
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card title={`${d.name} — ${d.result}`}>
        <KV rows={[
          ["Host", `${d.host.cpu_model} · ${d.host.vcpus} vCPU · ${d.host.mem_mb} MB · ${d.host.virtualization} · ${d.host.kernel}`],
          ["accel-ppp", d.accel_ppp],
          ["Sessions", `${s.established} / ${s.target} up, ${s.failed} failed, ${s.dropped_during_hold} dropped in ${s.hold_seconds}s hold`],
          ["Setup", `${s.setup_rate_per_s}/s (offered ${s.offered_rate_per_s}/s), latency p50 ${s.setup_latency_ms?.p50} / p95 ${s.setup_latency_ms?.p95} / p99 ${s.setup_latency_ms?.p99} ms`],
          ["Teardown", `${s.teardown_rate_per_s}/s`],
          ["Memory", `${s.mem_used_mb} MB for the sessions (${s.mem_per_session_kb} kB each)`],
          ["Idle hold CPU", `${s.cpu_hold?.cpu_avg_percent}% avg, accel-pppd ${s.cpu_hold?.accel_cpu_percent ?? "—"}%`],
          ...(t ? [
            ["Traffic", `${t.target_gbps} Gbit/s over ${t.sessions_used} sessions (${t.per_session_mbit} Mbit/s each)`],
            ["Delivered", `down ${g(t.down.gbps)} / up ${g(t.up.gbps)} Gbit/s, loss ${t.down.loss_percent}% / ${t.up.loss_percent}%`],
            ["PPS", `down ${fmtNum(t.down.pps)} / up ${fmtNum(t.up.pps)}`],
            ["Shaper drops", fmtNum(t.shaper_drops)],
          ] as [string, string][] : []),
          ...(d.tcp_max ? [["TCP maximum", `down ${g(d.tcp_max.down.gbps)} / up ${g(d.tcp_max.up.gbps)} Gbit/s`]] as [string, string][] : []),
          ["Radius latency", "not measured (aaa: lab)"],
        ]} />
        {d.fail_reasons.length > 0 && <ul className="mt-2 list-disc pl-4 text-xs text-red-300">{d.fail_reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      </Card>
      <Card title="CPU per core during traffic">
        {t ? (
          <div className="space-y-2">
            <Cores label="down busy %" values={t.down.host.cpu_per_core_percent} />
            <Cores label="down softirq %" values={t.down.host.softirq_per_core_percent} />
            <Cores label="up busy %" values={t.up.host.cpu_per_core_percent} />
            <div className="text-xs text-zinc-400">
              down: avg {t.down.host.cpu_avg_percent}%, busiest core {t.down.host.cpu_peak_core_percent_1s}% ·
              generators {t.down.host.generators_cpu_percent ?? "—"}% · accel-pppd {t.down.host.accel_cpu_percent ?? "—"}% ·
              softnet drops {t.down.host.softnet_drops} · squeezes {t.down.host.time_squeeze}
            </div>
          </div>
        ) : <Cores label="hold busy %" values={s.cpu_hold?.cpu_per_core_percent} />}
        <ul className="mt-3 list-disc space-y-1 pl-4 text-[11px] text-zinc-500">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
      </Card>
    </div>
  );
}

function TuningCards() {
  const { data, error } = useApi<Tuning>("/api/system/tuning", 30000);
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;
  const d = data.diagnostics;
  const rx = d.softirqs.NET_RX ?? [];
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card title="Tuning recommendations">
        <div className="mb-2 text-xs text-zinc-400">
          {d.cpus} CPUs · {d.mem_mb} MB · governor {d.governor ?? "n/a"} · irqbalance {d.irqbalance ?? "n/a"} ·{" "}
          {d.nics.map((n) => `${n.name} (${n.driver}) ${n.rx_queues} rx queue(s)`).join(", ")}
        </div>
        {data.recommendations.length ? (
          <table className={T.table}>
            <thead><tr>{["Setting", "Now", "Recommended", "", "Why"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
            <tbody>{data.recommendations.map((r) => (
              <tr key={r.key} className={T.tr}>
                <td className={`${T.td} font-mono`}>{r.key}</td><td className={`${T.td} font-mono`}>{r.current}</td>
                <td className={`${T.td} font-mono`}>{r.recommended}</td>
                <td className={T.td}><Badge tone={r.apply ? "info" : "warn"}>{r.apply ? "apply" : "manual"}</Badge></td>
                <td className={`${T.td} whitespace-normal text-zinc-400`}>{r.reason}</td>
              </tr>))}
            </tbody>
          </table>
        ) : <Empty>No recommendations: current settings match this hardware.</Empty>}
        <div className="mt-2 text-[11px] text-zinc-500">
          Apply the "apply" rows on the node with <code>sudo bngctl tuning apply</code> (all-or-nothing, read back),
          undo with <code>sudo bngctl tuning rollback</code>. {Object.keys(data.applied).length} setting(s) currently applied.
        </div>
      </Card>
      <Card title="CPU / IRQ distribution">
        <div className="overflow-x-auto">
          <table className={T.table}>
            <thead><tr><th className={T.th}></th>{d.softnet.map((r) => <th key={r.cpu} className={T.th}>cpu{r.cpu}</th>)}</tr></thead>
            <tbody>
              <tr className={T.tr}><td className={T.td}>softnet processed</td>{d.softnet.map((r) => <td key={r.cpu} className={`${T.td} font-mono`}>{fmtNum(r.processed)}</td>)}</tr>
              <tr className={T.tr}><td className={T.td}>softnet dropped</td>{d.softnet.map((r) => <td key={r.cpu} className={`${T.td} font-mono ${r.dropped ? "text-red-400" : ""}`}>{r.dropped}</td>)}</tr>
              <tr className={T.tr}><td className={T.td}>time squeeze</td>{d.softnet.map((r) => <td key={r.cpu} className={`${T.td} font-mono ${r.time_squeeze ? "text-amber-400" : ""}`}>{r.time_squeeze}</td>)}</tr>
              <tr className={T.tr}><td className={T.td}>NET_RX softirqs</td>{rx.map((v, i) => <td key={i} className={`${T.td} font-mono`}>{fmtNum(v)}</td>)}</tr>
              {d.irqs.map((q) => (
                <tr key={q.irq} className={T.tr}><td className={T.td}>IRQ {q.irq} {q.name}</td>{q.per_cpu.map((v, i) => <td key={i} className={`${T.td} font-mono`}>{fmtNum(v)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2 text-[11px] text-zinc-500">One column carrying all NET_RX work or IRQs means one CPU is the bottleneck.</div>
      </Card>
    </div>
  );
}

export default function Benchmark() {
  const { data, error } = useApi<Row[]>("/api/benchmarks", 30000);
  const [sel, setSel] = useState<string | null>(null);
  return (
    <>
      <PageHeader title="Benchmark" />
      <ErrorNote error={error} />
      <div className="space-y-3">
        <Card title="Results" actions={<span className="text-[11px] text-zinc-500">run on the node: sudo bngctl benchmark run --sessions N --traffic 1,5,10</span>}>
          {data?.length ? (
            <div className="overflow-x-auto">
              <table className={T.table}>
                <thead><tr>{["Run", "Date", "Sessions", "Target Gbit/s", "Down / up Gbit/s", "Loss %", "PPS down", "CPU avg / peak core", "Setup", "Result"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
                <tbody>{data.map((r) => (
                  <tr key={r.name} className={`${T.tr} cursor-pointer ${sel === r.name ? "bg-zinc-800/60" : ""}`} onClick={() => setSel(r.name)}>
                    <td className={`${T.td} font-mono`}>{r.name}</td><td className={T.td}>{fmtTime(r.date)}</td>
                    <td className={T.td}>{r.established} / {r.sessions}</td><td className={T.td}>{r.target_gbps ?? "—"}</td>
                    <td className={T.td}>{g(r.down_gbps)} / {g(r.up_gbps)}</td><td className={T.td}>{r.down_loss ?? "—"} / {r.up_loss ?? "—"}</td>
                    <td className={T.td}>{fmtNum(r.down_pps)}</td><td className={T.td}>{r.cpu_avg ?? "—"}% / {r.cpu_peak_core ?? "—"}%</td>
                    <td className={T.td}>{r.setup_rate_per_s}/s · p95 {r.setup_p95_ms} ms</td>
                    <td className={T.td} title={r.fail_reasons.join("\n")}><Badge tone={r.result === "PASS" ? "ok" : "bad"}>{r.result}</Badge></td>
                  </tr>))}
                </tbody>
              </table>
            </div>
          ) : <Empty>No benchmark has been run on this node.</Empty>}
        </Card>
        {sel && <Detail name={sel} />}
        <TuningCards />
      </div>
    </>
  );
}
