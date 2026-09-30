import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { token } from "../components/brand";
import { useLive, type Point } from "../components/context";
import { Card, Empty, fmtMbps, fmtNum, PageHeader, T } from "../components/ui";

const axis = () => ({ stroke: token("--z600"), fontSize: 10 });
const time = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function Chart({ title, data, lines, unit }: { title: string; data: Point[]; lines: [keyof Point, string, string][]; unit?: (v: number) => string }) {
  return (
    <Card title={title}>
      <div className="h-48">
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={token("--z800")} />
            <XAxis dataKey="t" tickFormatter={time} {...axis()} minTickGap={40} />
            <YAxis {...axis()} width={76} tickFormatter={unit ?? ((v) => fmtNum(v))} />
            <Tooltip contentStyle={{ background: token("--z900"), border: `1px solid ${token("--z700")}`, borderRadius: 8, fontSize: 11 }}
              labelFormatter={(t) => time(Number(t))} formatter={(v) => (unit ? unit(Number(v)) : fmtNum(Number(v), 1))} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {lines.map(([k, name, color]) => (
              <Line key={k} dataKey={k} name={name} stroke={color} dot={false} isAnimationActive={false} strokeWidth={1.5} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

export default function Monitoring() {
  const { live, history } = useLive();
  if (!live) return <Empty>Waiting for the first metrics update…</Empty>;
  return (
    <>
      <PageHeader title="Monitoring">
        <span className="text-[11px] text-zinc-500">
          live, last 15 min in this browser (2 s resolution) · long-term history: Prometheus/Zabbix
        </span>
      </PageHeader>
      <div className="grid gap-3 xl:grid-cols-2">
        <Chart title="Subscriber throughput" data={history} unit={fmtMbps}
          lines={[["down", "download", "var(--optic)"], ["up", "upload", "var(--amber)"]]} />
        <Chart title="Active sessions" data={history} lines={[["sessions", "active", "var(--ok)"]]} />
        <Chart title={`Uplink ${live.uplink} packets/s`} data={history} lines={[["rxpps", "rx", "var(--optic)"], ["txpps", "tx", "var(--amber)"]]} />
        <Chart title="CPU %" data={history} lines={[["cpu", "cpu", "var(--warn)"], ["softirq", "softirq", "var(--bad)"]]} />
        <Chart title="Conntrack entries" data={history} lines={[["conntrack", "entries", "var(--ok)"]]} />
        <Card title="Interfaces (live)">
          <table className={T.table}>
            <thead>
              <tr>{["NIC", "State", "Rx", "Tx", "Rx pps", "Tx pps", "Errors", "Drops"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {Object.entries(live.nics).map(([name, n]) => (
                <tr key={name} className={T.tr}>
                  <td className={T.td}>{name}</td>
                  <td className={`${T.td} ${n.state === "up" ? "text-emerald-400" : n.state === "down" ? "text-red-400" : "text-zinc-500"}`}>{n.state}</td>
                  <td className={`${T.td} num`}>{fmtMbps(n.rx_mbps)}</td>
                  <td className={`${T.td} num`}>{fmtMbps(n.tx_mbps)}</td>
                  <td className={`${T.td} num`}>{fmtNum(n.rx_pps)}</td>
                  <td className={`${T.td} num`}>{fmtNum(n.tx_pps)}</td>
                  <td className={`${T.td} num ${n.errors ? "text-amber-400" : ""}`}>{fmtNum(n.errors)}</td>
                  <td className={`${T.td} num ${n.drops ? "text-amber-400" : ""}`}>{fmtNum(n.drops)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}
