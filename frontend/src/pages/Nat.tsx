import type { Check } from "../api/types";
import { Card, CheckList, Empty, ErrorNote, fmtNum, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface Pool { name: string; subscribers: string[]; public_start: string; public_end: string | null; port_min: number; port_max: number; snat_target: string }

export default function Nat() {
  const status = useApi<{ check: Check; conntrack: Check; counters: { pool: string; packets: number; bytes: number }[] }>("/api/nat/status", 10000);
  const pools = useApi<Pool[]>("/api/nat/pools");
  const counter = (name: string) => status.data?.counters.find((c) => c.pool === name);
  return (
    <>
      <PageHeader title="NAT (CGNAT)" />
      <ErrorNote error={status.error ?? pools.error} />
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Status">{status.data ? <CheckList checks={[status.data.check, status.data.conntrack]} /> : <Empty />}</Card>
        <Card title="Pools" className="xl:col-span-2">
          {pools.data?.length ? (
            <table className={T.table}>
              <thead><tr>{["Pool", "Subscriber networks", "Public", "Ports", "Translated flows"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
              <tbody>
                {pools.data.map((p) => (
                  <tr key={p.name} className={T.tr}>
                    <td className={T.td}>{p.name}</td>
                    <td className={`${T.td} font-mono`}>{p.subscribers.join(", ")}</td>
                    <td className={`${T.td} font-mono`}>{p.public_end && p.public_end !== p.public_start ? `${p.public_start}–${p.public_end}` : p.public_start}</td>
                    <td className={`${T.td} num`}>{p.port_min}–{p.port_max}</td>
                    <td className={`${T.td} num`}>{fmtNum(counter(p.name)?.packets)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No NAT pools configured</Empty>}
          <p className="mt-2 text-[11px] text-zinc-500">The SNAT rule sees only the first packet of each flow (conntrack handles the rest), so this counts
            flows translated since the ruleset was loaded, not traffic. Pools are edited under Configuration.</p>
        </Card>
      </div>
    </>
  );
}
