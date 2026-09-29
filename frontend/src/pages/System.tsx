import { useState } from "react";
import { post } from "../api/client";
import { useAuth, useLive } from "../components/context";
import { Button, Card, CheckList, Empty, ErrorNote, fmtBytes, fmtDur, inputCls, KV, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface SystemStatus { node: string; accel_active: boolean; accel_version: string | null; accel: Record<string, unknown>; host: Record<string, unknown> }

function ChangePassword() {
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null); setMsg(null);
    try { await post("/api/auth/password", { current: cur, new: pw }); setMsg("Password changed; your other sessions were signed out."); setCur(""); setPw(""); }
    catch (x) { setErr((x as Error).message); }
  };
  return (
    <form onSubmit={submit} className="space-y-2">
      <input className={`${inputCls} w-full`} type="password" placeholder="current password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
      <input className={`${inputCls} w-full`} type="password" placeholder="new password (min 12)" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
      <Button disabled={!cur || pw.length < 12}>Change password</Button>
      <ErrorNote error={err} />
      {msg && <div className="text-xs text-emerald-400">{msg}</div>}
    </form>
  );
}

export default function System() {
  const { me } = useAuth();
  const { checks } = useLive();
  const { data, error } = useApi<SystemStatus>("/api/system/status", 15000);
  const h = (data?.host ?? {}) as { hostname?: string; uptime_s?: number; load?: number[]; mem_total_bytes?: number; mem_available_bytes?: number; cpus?: number };
  return (
    <>
      <PageHeader title="System" />
      <ErrorNote error={error} />
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Health (every 15 s)" className="xl:col-span-2">{checks ? <CheckList checks={checks} /> : <Empty>Running checks…</Empty>}</Card>
        <Card title="Node">
          {data ? <KV rows={[
            ["BNG", data.node], ["Hostname", h.hostname], ["Host uptime", fmtDur(h.uptime_s)], ["CPUs", h.cpus],
            ["Load", h.load?.join(" / ")], ["Memory", `${fmtBytes(h.mem_available_bytes)} free of ${fmtBytes(h.mem_total_bytes)}`],
            ["accel-ppp", data.accel_active ? `running — ${data.accel_version}` : "STOPPED"],
            ["accel uptime", String(data.accel.uptime ?? "—")], ["accel memory", String(data.accel["mem(rss/virt)"] ?? "—")],
          ]} /> : <Empty />}
        </Card>
        <Card title="accel-ppp core" className="xl:col-span-2">
          {data?.accel.core ? <KV rows={Object.entries(data.accel.core as Record<string, string>)} /> : <Empty />}
        </Card>
        <Card title={`Account — ${me.username} (${me.role})`}><ChangePassword /></Card>
      </div>
    </>
  );
}
