import type { Check } from "../api/types";
import { Card, CheckList, Empty, ErrorNote, fmtNum, KV, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface Pools { gw_ip_address: string; default: string; pools: { name: string; network: string; next: string | null; usable: number; used: number }[] }

export default function Pppoe() {
  const st = useApi<{ check: Check; interfaces: { name: string; padi_limit: number | null }[]; stats: Record<string, string> }>("/api/pppoe/status", 10000);
  const pools = useApi<Pools>("/api/ip-pools", 15000);
  return (
    <>
      <PageHeader title="PPPoE & IP pools" />
      <ErrorNote error={st.error ?? pools.error} />
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="PPPoE">
          {st.data ? <>
            <CheckList checks={[st.data.check]} />
            <table className={`${T.table} mt-3`}>
              <thead><tr><th className={T.th}>Interface</th><th className={T.th}>PADI limit</th></tr></thead>
              <tbody>{st.data.interfaces.map((i) => (
                <tr key={i.name} className={T.tr}><td className={T.td}>{i.name}</td><td className={`${T.td} num`}>{i.padi_limit ?? "default"}</td></tr>
              ))}</tbody>
            </table>
          </> : <Empty />}
        </Card>
        <Card title="accel-ppp PPPoE counters">{st.data ? <KV rows={Object.entries(st.data.stats)} /> : <Empty />}</Card>
        <Card title="IP pools">
          {pools.data ? <>
            <KV rows={[["Gateway", pools.data.gw_ip_address], ["Default pool", pools.data.default]]} />
            <table className={`${T.table} mt-3`}>
              <thead><tr>{["Pool", "Network", "Used", "Usable", "Use", "Next"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
              <tbody>{pools.data.pools.map((p) => {
                const pct = p.usable ? (100 * p.used) / p.usable : 0;
                return (
                  <tr key={p.name} className={T.tr}>
                    <td className={T.td}>{p.name}</td><td className={`${T.td} font-mono`}>{p.network}</td>
                    <td className={`${T.td} num`}>{fmtNum(p.used)}</td><td className={`${T.td} num`}>{fmtNum(p.usable)}</td>
                    <td className={`${T.td} num ${pct >= 90 ? "text-red-400" : pct >= 75 ? "text-amber-400" : ""}`}>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 rounded bg-zinc-800"><div className={`h-1.5 rounded ${pct >= 90 ? "bg-red-500" : pct >= 75 ? "bg-amber-400" : "bg-emerald-500"}`} style={{ width: `${Math.min(100, pct)}%` }} /></div>
                        {pct.toFixed(1)}%
                      </div>
                    </td>
                    <td className={T.td}>{p.next ?? "—"}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </> : <Empty />}
        </Card>
      </div>
    </>
  );
}
