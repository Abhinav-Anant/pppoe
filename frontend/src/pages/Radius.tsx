import { useEffect, useState } from "react";
import { post, put } from "../api/client";
import type { Check } from "../api/types";
import { useCan } from "../components/context";
import { Badge, Button, Card, CheckList, Empty, ErrorNote, inputCls, KV, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface Server { address: string; auth_port: number; acct_port: number; backup: boolean }
interface RadiusCfg {
  servers: Server[]; nas_identifier: string; nas_ip_address: string; coa_listen: string; coa_port: number;
  acct_interim_interval: number; status_server: boolean; [k: string]: unknown;
}
interface Status {
  aaa: string; radius: RadiusCfg | null; check: Check; secrets: Record<string, boolean>;
  stats: Record<string, Record<string, string> | string>;
}
type Shaper = Record<string, unknown>;

// How the RADIUS server tells the gateway a subscriber's plan speed. Every preset is parsed by
// accel-ppp 1.14.0 natively; values are kbit/s unless the multiplier says otherwise.
const PRESETS: { id: string; label: string; example: string; shaper: Shaper }[] = [
  { id: "filter-id", label: "Filter-Id", example: "Filter-Id = \"50000/25000\" (download/upload kbit/s)", shaper: { attr: "Filter-Id" } },
  { id: "mikrotik", label: "MikroTik rate limit", example: "Mikrotik-Rate-Limit = \"25M/50M\" (upload/download)",
    shaper: { attr: "Mikrotik-Rate-Limit", vendor: "Mikrotik" } },
  { id: "cisco", label: "Cisco AV-pair", example: "Cisco-AVPair = \"lcp:interface-config#1=rate-limit output 51200000 …\"",
    shaper: { attr: "Cisco-AVPair", vendor: "Cisco" } },
  { id: "wispr", label: "WISPr bandwidth", example: "WISPr-Bandwidth-Max-Down = 50000000 (bit/s)",
    shaper: { attr_down: "WISPr-Bandwidth-Max-Down", attr_up: "WISPr-Bandwidth-Max-Up", vendor: "WISPr", rate_multiplier: 0.001 } },
];
const RATE_KEYS = ["attr", "vendor", "attr_down", "attr_up", "rate_multiplier"];
const presetOf = (s: Shaper | null) => PRESETS.find((p) => Object.entries(p.shaper).every(([k, v]) => s?.[k] === v)
  && RATE_KEYS.every((k) => k in p.shaper || s?.[k] == null))?.id ?? "custom";

const blankServer = (): Server => ({ address: "", auth_port: 1812, acct_port: 1813, backup: false });

function Secrets({ status, onSaved }: { status: Status; onSaved: () => void }) {
  const can = useCan();
  const [target, setTarget] = useState("default");
  const [value, setValue] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const targets = ["default", ...(status.radius?.servers.map((s) => s.address) ?? [])];
  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null); setMsg(null);
    try {
      const r = await put<{ result: string }>("/api/radius/secret", { secret: value, ...(target === "default" ? {} : { server: target }) });
      setMsg(`Secret saved. ${r.result}`); setValue(""); onSaved();
    } catch (x) { setErr((x as Error).message); }
  };
  return (
    <Card title="Shared secrets">
      <ul className="mb-3 space-y-1.5 text-xs">
        {targets.map((t) => {
          const set = t === "default" ? status.secrets.default : status.secrets[t] ?? false;
          const inherits = t !== "default" && !status.secrets[t] && status.secrets.default;
          return (
            <li key={t} className="flex items-center justify-between">
              <span className="text-zinc-200">{t === "default" ? "Default (all servers)" : t}</span>
              {set ? <Badge tone="ok">Set</Badge> : inherits ? <Badge tone="dim">Uses default</Badge> : <Badge tone="warn">Not set</Badge>}
            </li>
          );
        })}
      </ul>
      {can("change_radius") && can("apply_config") ? (
        <form onSubmit={save} className="space-y-2">
          <div className="flex gap-2">
            <select className={inputCls} value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Secret for">
              {targets.map((t) => <option key={t} value={t}>{t === "default" ? "Default" : t}</option>)}
            </select>
            <input className={`${inputCls} min-w-0 flex-1`} type="password" autoComplete="off" placeholder="New shared secret"
              value={value} onChange={(e) => setValue(e.target.value)} />
            <Button variant="primary" disabled={value.length < 8}>Save secret</Button>
          </div>
          <p className="text-[11px] text-zinc-500">
            8 to 128 printable characters without spaces or commas. Stored on the gateway only; it is never shown again,
            logged or kept in configuration history.
          </p>
          <ErrorNote error={err} />
          {msg && <div className="text-xs text-emerald-400">{msg}</div>}
        </form>
      ) : <p className="text-[11px] text-zinc-500">Changing secrets needs the RADIUS and apply-configuration permissions.</p>}
    </Card>
  );
}

export default function RadiusPage() {
  const can = useCan();
  const { data, error, reload } = useApi<Status>("/api/radius/status", 15000);
  const cfg = useApi<{ config: Record<string, unknown> }>("/api/config");
  const [form, setForm] = useState<{ aaa: string; radius: RadiusCfg; preset: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!cfg.data || form) return;
    const c = cfg.data.config;
    const r = (c.radius as RadiusCfg | undefined) ?? {
      servers: [blankServer()], nas_identifier: String(c.node ?? "bng"), nas_ip_address: "", coa_listen: "",
      coa_port: 3799, acct_interim_interval: 300, status_server: true,
    };
    setForm({ aaa: String(c.aaa), radius: { ...r, status_server: r.status_server ?? true,
      servers: r.servers.map((x) => ({ ...blankServer(), ...x })) }, preset: presetOf((c.shaper as Shaper) ?? null) });
  }, [cfg.data, form]);

  if (!data || !form || !cfg.data) return <><PageHeader title="RADIUS" /><ErrorNote error={error ?? cfg.error} /><Empty>Loading…</Empty></>;

  const r = form.radius;
  const setR = (patch: Partial<RadiusCfg>) => setForm({ ...form, radius: { ...r, ...patch } });
  const setServer = (i: number, patch: Partial<Server>) => setR({ servers: r.servers.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const switching = form.aaa !== data.aaa;
  const editable = can("change_radius") && can("apply_config");

  const apply = async () => {
    setBusy(true); setErr(null); setMsg(null);
    const c: Record<string, unknown> = { ...cfg.data!.config, aaa: form.aaa };
    if (form.aaa === "radius") c.radius = { ...r, servers: r.servers.filter((s) => s.address) };
    const preset = PRESETS.find((p) => p.id === form.preset);
    if (preset && c.shaper) {
      const keep = Object.fromEntries(Object.entries(c.shaper as Shaper).filter(([k]) => !RATE_KEYS.includes(k)));
      c.shaper = { ...keep, ...preset.shaper };
    }
    try {
      const res = await post<{ result: string }>("/api/config/apply", { config: c, allow_restart: switching });
      setMsg(res.result === "no changes" ? "Nothing changed." : `Applied (${res.result}).${switching
        ? " Also allow CoA through the host firewall on the gateway: sudo bngctl firewall apply, then confirm." : ""}`);
      cfg.reload(); reload(); setForm(null);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader title="RADIUS" />
      <ErrorNote error={error} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Connection status" className="xl:col-span-2">
          <CheckList checks={[data.check]} />
          <p className="mt-2 text-[11px] text-zinc-500">
            Works with any RFC 2865/2866 server: FreeRADIUS, radiusdesk, Microsoft NPS, billing platforms. The probe is
            Status-Server (RFC 5997); turn it off below for servers that do not answer it.
          </p>
        </Card>
        <Card title="Authentication">
          <KV rows={[
            ["Mode", data.aaa === "radius" ? "RADIUS server" : "Local lab accounts"],
            ["Accounting interim", data.radius ? `${data.radius.acct_interim_interval} s` : "—"],
            ["Plan rate format", PRESETS.find((p) => p.id === presetOf((cfg.data!.config.shaper as Shaper) ?? null))?.label ?? "Custom"],
          ]} />
        </Card>

        <Card title="Connect a RADIUS server" className="xl:col-span-2"
          actions={editable && <Button variant="primary" onClick={apply} disabled={busy}>{busy ? "Applying…" : switching ? "Apply and restart" : "Apply"}</Button>}>
          <fieldset disabled={!editable} className="space-y-4">
            <div className="flex flex-wrap gap-4 text-xs">
              {[["radius", "Authenticate with RADIUS"], ["lab", "Local lab accounts (testing only)"]].map(([v, l]) => (
                <label key={v} className="flex items-center gap-2 text-zinc-200">
                  <input type="radio" name="aaa" checked={form.aaa === v} onChange={() => setForm({ ...form, aaa: v })} />{l}
                </label>
              ))}
            </div>
            {form.aaa === "radius" && (
              <>
                <table className={T.table}>
                  <thead><tr>{["Server address", "Auth port", "Accounting port", "Role", ""].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {r.servers.map((s, i) => (
                      <tr key={i}>
                        <td className={T.td}><input className={`${inputCls} w-40`} value={s.address} placeholder="10.0.0.10" onChange={(e) => setServer(i, { address: e.target.value })} /></td>
                        <td className={T.td}><input className={`${inputCls} w-20`} type="number" value={s.auth_port} onChange={(e) => setServer(i, { auth_port: Number(e.target.value) })} /></td>
                        <td className={T.td}><input className={`${inputCls} w-20`} type="number" value={s.acct_port} onChange={(e) => setServer(i, { acct_port: Number(e.target.value) })} /></td>
                        <td className={T.td}>
                          <select className={inputCls} value={s.backup ? "backup" : "primary"} onChange={(e) => setServer(i, { backup: e.target.value === "backup" })}>
                            <option value="primary">Primary</option><option value="backup">Backup</option>
                          </select>
                        </td>
                        <td className={T.td}>{r.servers.length > 1 && <Button variant="ghost" onClick={() => setR({ servers: r.servers.filter((_, j) => j !== i) })}>Remove</Button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {r.servers.length < 4 && <Button onClick={() => setR({ servers: [...r.servers, blankServer()] })}>Add server</Button>}
                <div className="grid gap-3 text-xs sm:grid-cols-2">
                  {([["nas_identifier", "NAS-Identifier (how the server knows this gateway)"], ["nas_ip_address", "NAS-IP-Address"],
                    ["coa_listen", "Listen for CoA and disconnect on"]] as const).map(([k, l]) => (
                    <label key={k} className="text-zinc-400">{l}
                      <input className={`${inputCls} mt-1 w-full`} value={String(r[k] ?? "")} onChange={(e) => setR({ [k]: e.target.value })} />
                    </label>
                  ))}
                  <label className="text-zinc-400">Accounting interim interval (seconds)
                    <input className={`${inputCls} mt-1 w-full`} type="number" value={r.acct_interim_interval}
                      onChange={(e) => setR({ acct_interim_interval: Number(e.target.value) })} />
                  </label>
                </div>
                <label className="flex items-center gap-2 text-xs text-zinc-200">
                  <input type="checkbox" checked={r.status_server} onChange={(e) => setR({ status_server: e.target.checked })} />
                  The server answers Status-Server health probes
                </label>
              </>
            )}
            <div>
              <div className="mb-1.5 text-xs font-medium text-zinc-300">Plan speed attribute</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {PRESETS.map((p) => (
                  <label key={p.id} className={`cursor-pointer rounded-lg border px-3 py-2 text-xs ${form.preset === p.id ? "border-sky-500 bg-sky-950" : "border-zinc-800 hover:border-zinc-700"}`}>
                    <input type="radio" name="preset" className="sr-only" checked={form.preset === p.id} onChange={() => setForm({ ...form, preset: p.id })} />
                    <div className="font-medium text-zinc-100">{p.label}</div>
                    <div className="mt-0.5 break-all text-[11px] text-zinc-500">{p.example}</div>
                  </label>
                ))}
              </div>
              {form.preset === "custom" && <p className="mt-2 text-[11px] text-zinc-500">A custom attribute is set in Configuration; picking a preset replaces it.</p>}
            </div>
            {switching && (
              <div className="rounded-lg border border-amber-700 bg-amber-950 px-3 py-2 text-xs text-amber-300">
                Changing the authentication mode restarts the gateway daemon and disconnects every subscriber; they reconnect on their own.
              </div>
            )}
            <ErrorNote error={err} />
            {msg && <div className="text-xs text-emerald-400">{msg}</div>}
          </fieldset>
        </Card>
        {data.radius ? <Secrets status={data} onSaved={reload} /> : <Card title="Shared secrets"><Empty>Switch to RADIUS to set secrets.</Empty></Card>}

        {Object.entries(data.stats).map(([name, v]) => (
          <Card key={name} title={`Server ${name.replace(/^radius\(\d+,\s*/, "").replace(/\)$/, "")}`}>
            <KV rows={typeof v === "string" ? [[name, v]] : Object.entries(v)} />
          </Card>
        ))}
      </div>
    </>
  );
}
