import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get, post } from "../api/client";
import type { Page, Session } from "../api/types";
import { useCan, useLive } from "../components/context";
import { Badge, Button, Empty, ErrorNote, fmtBytes, fmtDur, fmtKbit, fmtMbps, fmtNum, inputCls, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useSocket } from "../websocket/useSocket";

type Sort = "username" | "ip" | "uptime_s" | "download_mbps" | "upload_mbps" | "download_bytes" | "upload_bytes" | "vlan";
const SEARCH_RE = /^[A-Za-z0-9_.@:-]{0,64}$/;

// Columns: [header, sort key or null, cell]
const COLS: [string, Sort | null, (r: Session) => React.ReactNode][] = [
  ["Username", "username", (r) => <Link className="text-sky-400 hover:underline" to={`/sessions/${r.sid}`}>{r.username}</Link>],
  ["Session ID", null, (r) => <span className="font-mono text-zinc-400">{r.sid}</span>],
  ["IP", "ip", (r) => r.ip ?? "—"],
  ["IPv6", null, (r) => r.ip6 ?? r.ip6_delegated ?? "—"],
  ["MAC", null, (r) => <span className="font-mono">{r.mac}</span>],
  ["Interface", null, (r) => `${r.inbound_if ?? "—"} · ${r.ifname}`],
  ["VLAN", "vlan", (r) => r.vlan ?? "—"],
  ["Uptime", "uptime_s", (r) => fmtDur(r.uptime_s)],
  ["Down", "download_mbps", (r) => fmtMbps(r.download_mbps)],
  ["Up", "upload_mbps", (r) => fmtMbps(r.upload_mbps)],
  ["Down bytes", "download_bytes", (r) => fmtBytes(r.download_bytes)],
  ["Up bytes", "upload_bytes", (r) => fmtBytes(r.upload_bytes)],
  ["Plan", null, (r) => (r.rate_down_kbit == null ? <Badge tone="warn">none</Badge> : `${fmtKbit(r.rate_down_kbit)}/${fmtKbit(r.rate_up_kbit)}`)],
  ["Status", null, (r) => (
    <span className="flex gap-1">
      <Badge tone={r.state === "active" ? "ok" : "warn"}>{r.state}</Badge>
      {r.duplicate && <Badge tone="bad">dup</Badge>}
    </span>
  )],
];

export default function Sessions() {
  const can = useCan();
  const { live } = useLive();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [q, setQ] = useState(search);
  const [state, setState] = useState(params.get("state") ?? "");
  const [dups, setDups] = useState(params.get("dup") === "1");
  const [sort, setSort] = useState<Sort>((params.get("sort") as Sort) ?? "username");
  const [desc, setDesc] = useState(params.get("desc") === "1");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(100);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {  // debounce typing; the server filters, the browser never holds every session
    const t = window.setTimeout(() => SEARCH_RE.test(search) && (setQ(search), setPage(1)), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const query = useMemo(() => {
    const p = new URLSearchParams({ sort, desc: String(desc), page: String(page), page_size: String(pageSize) });
    if (q) p.set("search", q);
    if (state) p.set("state", state);
    if (dups) p.set("duplicates", "true");
    return p.toString();
  }, [q, state, dups, sort, desc, page, pageSize]);

  useEffect(() => {
    setParams({ ...(q && { q }), ...(state && { state }), ...(dups && { dup: "1" }), sort, ...(desc && { desc: "1" }) }, { replace: true });
  }, [q, state, dups, sort, desc, setParams]);

  const { data, error: loadError, reload, setData } = useApi<Page<Session>>(`/api/sessions?${query}`, 30000);

  // live counters for the visible rows only
  const { send } = useSocket<{ updates: (Partial<Session> & { sid: string; gone?: boolean })[] }>("/api/ws/sessions", (m) => {
    if (!m.updates.length) return;
    setData((d) => d && {
      ...d,
      items: d.items.map((r) => {
        const u = m.updates.find((x) => x.sid === r.sid);
        return u ? (u.gone ? { ...r, state: "ended" } : { ...r, ...u }) : r;
      }),
    });
  });
  const sids = data?.items.map((r) => r.sid).join(",") ?? "";
  useEffect(() => send({ sids: sids ? sids.split(",") : [] }), [sids]); // eslint-disable-line react-hooks/exhaustive-deps

  const sortBy = (k: Sort) => {
    if (k === sort) setDesc(!desc);
    else (setSort(k), setDesc(k !== "username" && k !== "ip" && k !== "vlan"));
    setPage(1);
  };

  const disconnect = async (r: Session, hard = false) => {
    if (!window.confirm(`Disconnect ${r.username} (${r.ip ?? r.sid})${hard ? " — HARD" : ""}?`)) return;
    try {
      await post(`/api/sessions/${r.sid}/disconnect`, { hard });
      setTimeout(reload, 2500);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const exportCsv = async () => {
    const rows: Session[] = [];
    for (let p = 1; ; p++) {
      const base = new URLSearchParams(query);
      base.set("page", String(p));
      base.set("page_size", "1000");
      const r = await get<Page<Session>>(`/api/sessions?${base}`);
      rows.push(...r.items);
      if (rows.length >= r.total || !r.items.length) break;
    }
    const keys: (keyof Session)[] = ["username", "sid", "ip", "ip6", "ip6_delegated", "mac", "inbound_if", "vlan", "ifname",
      "state", "uptime_s", "download_bytes", "upload_bytes", "download_mbps", "upload_mbps", "rate_down_kbit", "rate_up_kbit", "duplicate"];
    const esc = (v: unknown) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
    const csv = [["node", ...keys].join(","), ...rows.map((r) => [live?.node ?? "", ...keys.map((k) => r[k])].map(esc).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `sessions-${live?.node ?? "bng"}-${new Date().toISOString().slice(0, 19)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  return (
    <>
      <PageHeader title="Sessions">
        <input className={`${inputCls} w-56 ${SEARCH_RE.test(search) ? "" : "border-red-600"}`} placeholder="user, IP, MAC, interface" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select className={inputCls} value={state} onChange={(e) => (setState(e.target.value), setPage(1))}>
          <option value="">all states</option>
          <option value="active">active</option>
          <option value="start">starting</option>
          <option value="finish">finishing</option>
        </select>
        <label className="flex items-center gap-1 text-xs text-zinc-400">
          <input type="checkbox" checked={dups} onChange={(e) => (setDups(e.target.checked), setPage(1))} /> duplicates
        </label>
        <Button onClick={reload}>Refresh</Button>
        <Button onClick={() => exportCsv().catch((e) => setError(e.message))}>Export CSV</Button>
      </PageHeader>
      <ErrorNote error={error ?? loadError} />
      <div className="overflow-auto rounded border border-zinc-800" style={{ maxHeight: "calc(100vh - 150px)" }}>
        <table className={T.table}>
          <thead>
            <tr>
              <th className={T.th}>BNG</th>
              {COLS.map(([h, key]) => (
                <th key={h} className={`${T.th} ${key ? "cursor-pointer select-none hover:text-zinc-300" : ""}`} onClick={() => key && sortBy(key)}>
                  {h}{key === sort ? (desc ? " ↓" : " ↑") : ""}
                </th>
              ))}
              {can("disconnect_sessions") && <th className={T.th} />}
            </tr>
          </thead>
          <tbody>
            {data?.items.map((r) => (
              <tr key={r.sid} className={`${T.tr} ${r.state === "ended" ? "opacity-40" : ""}`}>
                <td className={`${T.td} text-zinc-500`}>{live?.node}</td>
                {COLS.map(([h, , cell]) => <td key={h} className={`${T.td} num`}>{cell(r)}</td>)}
                {can("disconnect_sessions") && (
                  <td className={T.td}>
                    <Button variant="danger" className="px-1.5 py-0" disabled={r.state === "ended"} onClick={() => disconnect(r)}>Disconnect</Button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {data && !data.items.length && <Empty>No sessions match</Empty>}
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs text-zinc-400">
        <span className="num">{fmtNum(data?.total)} sessions</span>
        <span className="flex-1" />
        <select className={inputCls} value={pageSize} onChange={(e) => (setPageSize(Number(e.target.value)), setPage(1))}>
          {[50, 100, 250, 500].map((n) => <option key={n} value={n}>{n} / page</option>)}
        </select>
        <Button disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</Button>
        <span className="num">{page} / {pages}</span>
        <Button disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</Button>
      </div>
    </>
  );
}
