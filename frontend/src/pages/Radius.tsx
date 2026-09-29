import type { Check } from "../api/types";
import { Card, CheckList, Empty, ErrorNote, KV, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface Server { address: string; auth_port: number; acct_port: number; backup: boolean }
interface Radius {
  aaa: string;
  radius: null | { servers: Server[]; nas_identifier: string; nas_ip_address: string; timeout: number; max_try: number;
    acct_interim_interval: number; coa_listen: string; coa_port: number; dae_allowed: string[] };
  check: Check;
  stats: Record<string, Record<string, string> | string>;
}

export default function RadiusPage() {
  const { data, error } = useApi<Radius>("/api/radius/status", 15000);
  return (
    <>
      <PageHeader title="RADIUS (Jaze)" />
      <ErrorNote error={error} />
      {!data ? <Empty>Loading…</Empty> : (
        <div className="grid gap-3 xl:grid-cols-2">
          <Card title="Status">
            <CheckList checks={[data.check]} />
            <p className="mt-2 text-[11px] text-zinc-500">Probe: RADIUS Status-Server with Message-Authenticator. The shared secret never leaves the node.</p>
          </Card>
          <Card title="NAS settings">
            {data.radius ? (
              <KV rows={[
                ["AAA mode", data.aaa], ["NAS-Identifier", data.radius.nas_identifier], ["NAS-IP-Address", data.radius.nas_ip_address],
                ["Timeout / tries", `${data.radius.timeout}s × ${data.radius.max_try}`], ["Interim interval", `${data.radius.acct_interim_interval}s`],
                ["CoA / DM listen", `${data.radius.coa_listen}:${data.radius.coa_port}`],
                ["CoA allowed from", data.radius.dae_allowed.length ? data.radius.dae_allowed.join(", ") : "the RADIUS servers"],
              ]} />
            ) : <KV rows={[["AAA mode", `${data.aaa} (local chap-secrets, lab only)`]]} />}
          </Card>
          <Card title="Servers" className="xl:col-span-2">
            {data.radius?.servers.length ? (
              <table className={T.table}>
                <thead><tr>{["Address", "Auth port", "Acct port", "Role"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
                <tbody>
                  {data.radius.servers.map((s) => (
                    <tr key={s.address} className={T.tr}>
                      <td className={`${T.td} font-mono`}>{s.address}</td><td className={`${T.td} num`}>{s.auth_port}</td>
                      <td className={`${T.td} num`}>{s.acct_port}</td><td className={T.td}>{s.backup ? "backup" : "primary"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty>No RADIUS servers configured</Empty>}
          </Card>
          {Object.entries(data.stats).map(([name, v]) => (
            <Card key={name} title={`accel-ppp ${name}`}>
              <KV rows={typeof v === "string" ? [[name, v]] : Object.entries(v)} />
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
