import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { post } from "../api/client";
import type { Session } from "../api/types";
import { token } from "../components/brand";
import { useCan, useLive } from "../components/context";
import { Badge, Button, Card, Empty, ErrorNote, fmtBytes, fmtDur, fmtKbit, fmtMbps, fmtNum, KV, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useSocket } from "../websocket/useSocket";

interface P { t: number; down: number | null; up: number | null }

export default function SessionDetail() {
  const { sid = "" } = useParams();
  const can = useCan();
  const nav = useNavigate();
  const { live } = useLive();
  const { data: s, error, reload, setData } = useApi<Session>(`/api/sessions/${sid}`);
  const [gone, setGone] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const trail = useRef<P[]>([]);
  const [, tick] = useState(0);

  // traffic graph: live counters for this one session, kept while the page is open
  useSocket<{ updates: (Partial<Session> & { sid: string; gone?: boolean })[] }>(
    "/api/ws/sessions",
    (m) => {
      const u = m.updates.find((x) => x.sid === sid);
      if (!u) return;
      if (u.gone) return setGone(true);
      setData((d) => (d ? { ...d, ...u } : d));
      trail.current = [...trail.current.slice(-450), { t: Date.now() / 1000, down: u.download_mbps ?? null, up: u.upload_mbps ?? null }];
      tick((n) => n + 1);
    },
    { sids: [sid] },
  );

  const disconnect = async (hard: boolean) => {
    if (!s || !window.confirm(`Disconnect ${s.username}${hard ? " (hard)" : ""}?`)) return;
    try {
      await post(`/api/sessions/${sid}/disconnect`, { hard });
      setMsg("Disconnect sent.");
      setTimeout(() => nav("/sessions"), 1500);
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  if (error) return <><PageHeader title="Session" /><ErrorNote error={error} /><Link className="text-xs text-sky-400" to="/sessions">← sessions</Link></>;
  if (!s) return <Empty>Loading…</Empty>;
  const time = (t: number) => new Date(t * 1000).toLocaleTimeString();
  return (
    <>
      <PageHeader title={`Session — ${s.username}`}>
        {gone && <Badge tone="bad">session ended</Badge>}
        <Button onClick={reload}>Refresh</Button>
        <Link to={`/sessions?q=${encodeURIComponent(s.username)}`}><Button>Other sessions of this user</Button></Link>
        {can("disconnect_sessions") && <>
          <Button variant="danger" onClick={() => disconnect(false)}>Disconnect</Button>
          <Button variant="danger" onClick={() => disconnect(true)}>Hard disconnect</Button>
        </>}
      </PageHeader>
      <ErrorNote error={msg && !msg.startsWith("Disconnect sent") ? msg : null} />
      {msg?.startsWith("Disconnect sent") && <div className="mb-2 text-xs text-emerald-400">{msg}</div>}
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Subscriber">
          <KV rows={[
            ["Username", s.username], ["Session ID", <span className="font-mono">{s.sid}</span>], ["State", <Badge tone={s.state === "active" ? "ok" : "warn"}>{s.state}</Badge>],
            ["IPv4", s.ip ?? "—"], ["IPv6", s.ip6 ?? "—"], ["IPv6 delegated", s.ip6_delegated ?? "—"], ["MAC", <span className="font-mono">{s.mac}</span>],
            ["BNG", live?.node ?? "—"], ["Interface", s.inbound_if ?? "—"], ["VLAN", s.vlan ?? "—"], ["PPP interface", s.ifname],
            ["Login time", s.uptime_s != null ? new Date(Date.now() - s.uptime_s * 1000).toLocaleString() : "—"], ["Uptime", fmtDur(s.uptime_s)],
            ["Duplicate", s.duplicate ? <Badge tone="bad">same user or MAC in another session</Badge> : "no"],
          ]} />
        </Card>
        <Card title="Traffic">
          <KV rows={[
            ["Current download", fmtMbps(s.download_mbps)], ["Current upload", fmtMbps(s.upload_mbps)],
            ["Peak download", fmtMbps(s.peak_download_mbps)], ["Peak upload", fmtMbps(s.peak_upload_mbps)],
            ["Downloaded", `${fmtBytes(s.download_bytes)} · ${fmtNum(s.download_packets)} pkts`],
            ["Uploaded", `${fmtBytes(s.upload_bytes)} · ${fmtNum(s.upload_packets)} pkts`],
          ]} />
          <p className="mt-2 text-[11px] text-zinc-500">Peaks since this API process first saw the session (2 s samples).</p>
        </Card>
        <Card title="Plan (from RADIUS)">
          <KV rows={[
            ["Download limit", fmtKbit(s.rate_down_kbit)], ["Upload limit", fmtKbit(s.rate_up_kbit)], ["accel rate-limit", s.rate_limit ?? "— (unshaped)"],
          ]} />
          <p className="mt-2 text-[11px] text-zinc-500">
            accel-ppp 1.14.0 does not expose the other RADIUS reply attributes per session; Jaze remains the source for those.
          </p>
        </Card>
        <Card title="Live traffic (this page)" className="xl:col-span-3">
          {trail.current.length < 2 ? <Empty>Collecting samples…</Empty> : (
            <div className="h-56">
              <ResponsiveContainer>
                <LineChart data={trail.current}>
                  <CartesianGrid stroke={token("--z800")} />
                  <XAxis dataKey="t" tickFormatter={time} stroke={token("--z600")} fontSize={10} minTickGap={40} />
                  <YAxis stroke={token("--z600")} fontSize={10} width={56} tickFormatter={(v) => fmtMbps(v)} />
                  <Tooltip contentStyle={{ background: token("--z900"), border: `1px solid ${token("--z700")}`, borderRadius: 8, fontSize: 11 }} labelFormatter={(t) => time(Number(t))} formatter={(v) => fmtMbps(Number(v))} />
                  <Line dataKey="down" name="download" stroke="var(--optic)" dot={false} isAnimationActive={false} />
                  <Line dataKey="up" name="upload" stroke="var(--amber)" dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
