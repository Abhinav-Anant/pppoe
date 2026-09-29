import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, get, post } from "../api/client";
import type { Live, Session } from "../api/types";
import { useCan, useNode } from "../components/context";
import { Badge, Button, Card, Dot, Empty, ErrorNote, fmtDur, fmtKbit, fmtMbps, fmtNum, inputCls, KV, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface FleetRow { name: string; local: boolean; ok: boolean; error?: string; live?: Live }

const pct = (a: number | null | undefined, b: number | null | undefined) => (a != null && b ? (100 * a) / b : null);

function Compare({ rows, open }: { rows: FleetRow[]; open: (n: string) => void }) {
  const metric: [string, (l: Live) => React.ReactNode][] = [
    ["accel-ppp", (l) => (l.accel_active ? <span className="text-emerald-400">running</span> : <span className="text-red-400">STOPPED</span>)],
    ["Active sessions", (l) => fmtNum(l.sessions.active)],
    ["Logins / logouts per min", (l) => `${l.sessions.logins_per_min} / ${l.sessions.logouts_per_min}`],
    ["Subscriber down", (l) => fmtMbps(l.sessions.download_mbps)],
    ["Subscriber up", (l) => fmtMbps(l.sessions.upload_mbps)],
    ["Uplink rx / tx", (l) => `${fmtMbps(l.nics[l.uplink]?.rx_mbps)} / ${fmtMbps(l.nics[l.uplink]?.tx_mbps)}`],
    ["Uplink pps rx / tx", (l) => `${fmtNum(l.nics[l.uplink]?.rx_pps)} / ${fmtNum(l.nics[l.uplink]?.tx_pps)}`],
    ["CPU / softirq", (l) => `${l.host.cpu_percent ?? "—"}% / ${l.host.softirq_percent ?? "—"}%`],
    ["RAM used", (l) => { const p = pct(l.host.mem_total_bytes && l.host.mem_available_bytes != null ? l.host.mem_total_bytes - l.host.mem_available_bytes : null, l.host.mem_total_bytes); return p == null ? "—" : `${p.toFixed(0)}%`; }],
    ["Conntrack", (l) => { const p = pct(l.conntrack.count, l.conntrack.max); return p == null ? "—" : `${p.toFixed(1)}%`; }],
    ["NIC errors / drops", (l) => `${fmtNum(l.nics[l.uplink]?.errors)} / ${fmtNum(l.nics[l.uplink]?.drops)}`],
    ["Duplicates / unshaped", (l) => `${l.sessions.duplicates} / ${l.sessions.unshaped}`],
    ["accel uptime", (l) => l.accel.uptime ?? "—"],
    ["Host uptime", (l) => fmtDur(l.host.uptime_s)],
  ];
  return (
    <div className="overflow-auto">
      <table className={T.table}>
        <thead>
          <tr>
            <th className={T.th} />
            {rows.map((r) => (
              <th key={r.name} className={T.th}>
                <button className="flex items-center gap-1.5 text-zinc-200 hover:text-sky-400" onClick={() => open(r.name)}>
                  <Dot tone={!r.ok ? "bad" : r.live?.accel_active ? "ok" : "bad"} />{r.name}{r.local && <span className="text-[10px] font-normal normal-case text-zinc-500">this node</span>}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metric.map(([label, cell]) => (
            <tr key={label} className={T.tr}>
              <td className={`${T.td} text-zinc-500`}>{label}</td>
              {rows.map((r) => (
                <td key={r.name} className={`${T.td} num`}>{r.ok && r.live ? cell(r.live) : label === "accel-ppp" ? <span className="text-red-400" title={r.error}>unreachable</span> : "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FindSubscriber({ open }: { open: (node: string, sid?: string) => void }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<{ items: (Session & { node: string })[]; errors: Record<string, string> } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try { setRes(await get(`/api/fleet/sessions?search=${encodeURIComponent(q)}`)); } catch (x) { setErr((x as Error).message); }
  };
  return (
    <>
      <form onSubmit={search} className="mb-2 flex gap-2">
        <input className={`${inputCls} flex-1`} placeholder="username, IP, MAC on every BNG" value={q} onChange={(e) => setQ(e.target.value)} />
        <Button disabled={!/^[A-Za-z0-9_.@:-]{1,64}$/.test(q)}>Search</Button>
      </form>
      <ErrorNote error={err} />
      {res && Object.entries(res.errors).map(([n, e]) => <div key={n} className="text-[11px] text-amber-400">{n}: {e}</div>)}
      {res && (res.items.length ? (
        <table className={T.table}>
          <thead><tr>{["BNG", "User", "IP", "MAC", "VLAN", "Plan", "Uptime"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
          <tbody>{res.items.map((s) => (
            <tr key={`${s.node}/${s.sid}`} className={`${T.tr} cursor-pointer`} onClick={() => open(s.node, s.sid)}>
              <td className={T.td}>{s.node}</td><td className={`${T.td} text-sky-400`}>{s.username}</td><td className={`${T.td} num`}>{s.ip ?? "—"}</td>
              <td className={`${T.td} font-mono`}>{s.mac}</td><td className={T.td}>{s.vlan ?? "—"}</td>
              <td className={T.td}>{fmtKbit(s.rate_down_kbit)}/{fmtKbit(s.rate_up_kbit)}</td><td className={T.td}>{fmtDur(s.uptime_s)}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : <Empty>No match on any BNG</Empty>)}
    </>
  );
}

function AddNode() {
  const { reloadNodes } = useNode();
  const [f, setF] = useState({ name: "", url: "https://", token: "" });
  const [fp, setFp] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const probe = async () => {
    setErr(null); setFp(null); setConfirmed(false);
    try { setFp((await post<{ fingerprint: string }>("/api/nodes/probe", { url: f.url })).fingerprint); } catch (x) { setErr((x as Error).message); }
  };
  const add = async () => {
    setErr(null);
    try {
      const r = await post<{ node_role: string }>("/api/nodes", { ...f, fingerprint: fp });
      setMsg(`${f.name} added (token role on the node: ${r.node_role})`);
      setF({ name: "", url: "https://", token: "" }); setFp(null); setConfirmed(false);
      reloadNodes();
    } catch (x) { setErr((x as Error).message); }
  };
  return (
    <div className="space-y-2">
      <input className={`${inputCls} w-full`} placeholder="name (e.g. bng02)" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <div className="flex gap-2">
        <input className={`${inputCls} flex-1`} placeholder="https://10.0.0.2:8443" value={f.url} onChange={(e) => (setF({ ...f, url: e.target.value }), setFp(null))} />
        <Button onClick={probe} disabled={!/^https:\/\/[A-Za-z0-9.-]+(:\d+)?$/.test(f.url)}>Get certificate</Button>
      </div>
      {fp && (
        <div className="rounded border border-amber-800 bg-amber-950/40 p-2 text-[11px]">
          <div className="text-amber-300">Compare with <code>sudo bngctl tls fingerprint</code> on the node:</div>
          <div className="mt-1 break-all font-mono text-zinc-200">{fp}</div>
          <label className="mt-1 flex items-center gap-1 text-zinc-300"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> it matches</label>
        </div>
      )}
      <input className={`${inputCls} w-full font-mono`} type="password" autoComplete="off" placeholder="token from: sudo bngctl token create <console> --role network_admin"
        value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} />
      <Button variant="primary" disabled={!fp || !confirmed || !f.name || !f.token} onClick={add}>Add node</Button>
      <ErrorNote error={err} />
      {msg && <div className="text-xs text-emerald-400">{msg}</div>}
    </div>
  );
}

export default function Bng() {
  const { id } = useParams();
  const can = useCan();
  const nav = useNavigate();
  const { nodes, select, reloadNodes } = useNode();
  const { data, error } = useApi<FleetRow[]>("/api/fleet", 5000);

  const open = (name: string, sid?: string) => { select(name); nav(sid ? `/sessions/${sid}` : "/dashboard"); };
  useEffect(() => { if (id && nodes.some((n) => n.name === id)) open(id); }, [id, nodes]); // eslint-disable-line react-hooks/exhaustive-deps

  const remove = async (name: string) => {
    if (!window.confirm(`Remove ${name} from this console? (the node itself is not changed; revoke its token there)`)) return;
    await api(`/api/nodes/${encodeURIComponent(name)}`, { method: "DELETE" }).catch(() => {});
    reloadNodes();
  };

  const rows = data ?? [];
  const total = (f: (l: Live) => number) => rows.reduce((a, r) => a + (r.live ? f(r.live) : 0), 0);
  return (
    <>
      <PageHeader title="All BNGs">
        <span className="text-[11px] text-zinc-500">{rows.filter((r) => r.ok).length}/{rows.length} reachable · refresh 5 s</span>
      </PageHeader>
      <ErrorNote error={error} />
      <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-4">
        {[["BNGs", fmtNum(rows.length)], ["Active sessions", fmtNum(total((l) => l.sessions.active))],
          ["Subscriber down", fmtMbps(total((l) => l.sessions.download_mbps))], ["Subscriber up", fmtMbps(total((l) => l.sessions.upload_mbps))]]
          .map(([k, v]) => (
            <div key={k} className="rounded border border-zinc-800 bg-zinc-900/60 px-3 py-2">
              <div className="text-[11px] uppercase tracking-wider text-zinc-500">{k}</div>
              <div className="num text-lg font-semibold text-zinc-100">{v}</div>
            </div>
          ))}
      </div>
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Compare" className="xl:col-span-2">{rows.length ? <Compare rows={rows} open={open} /> : <Empty>Loading…</Empty>}</Card>
        <Card title="Nodes">
          <table className={T.table}>
            <tbody>
              {nodes.map((n) => {
                const r = rows.find((x) => x.name === n.name);
                return (
                  <tr key={n.name} className={T.tr}>
                    <td className={T.td}><span className="flex items-center gap-1.5"><Dot tone={!r ? "dim" : r.ok ? "ok" : "bad"} />{n.name}</span></td>
                    <td className={`${T.td} text-zinc-500`}>{n.local ? "this node" : n.url}</td>
                    <td className={`${T.td} text-right`}>
                      <Button className="px-1.5 py-0" onClick={() => open(n.name)}>Open</Button>
                      {!n.local && can("manage_nodes") && <Button variant="danger" className="ml-1 px-1.5 py-0" onClick={() => remove(n.name)}>Remove</Button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.filter((r) => !r.ok).map((r) => <div key={r.name} className="mt-1 text-[11px] text-red-400">{r.name}: {r.error}</div>)}
          {nodes.some((n) => !n.local) && (
            <div className="mt-2"><KV rows={nodes.filter((n) => !n.local).map((n) => [`${n.name} cert`, <span className="font-mono text-[10px]">{n.fingerprint?.slice(0, 32)}…</span>])} /></div>
          )}
        </Card>
        {can("view_sessions") && <Card title="Find a subscriber on any BNG" className="xl:col-span-2"><FindSubscriber open={open} /></Card>}
        {can("manage_nodes") && <Card title={<>Add a BNG <Badge>TLS pinned</Badge></>}><AddNode /></Card>}
      </div>
    </>
  );
}
