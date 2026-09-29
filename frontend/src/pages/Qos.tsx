import { useEffect, useState } from "react";
import { put } from "../api/client";
import type { Check } from "../api/types";
import { useCan } from "../components/context";
import { Badge, Button, Card, CheckList, Empty, ErrorNote, fmtKbit, fmtNum, inputCls, KV, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface Shaper { attr: string; vendor: string | null; down_limiter: string; up_limiter: string; max_rate_mbit: number | null; require_rate: boolean }
interface Status { shaper: Shaper | null; check: Check; plans: Record<string, number> }

export default function Qos() {
  const can = useCan();
  const { data, error, reload } = useApi<Status>("/api/qos/status", 10000);
  const [form, setForm] = useState<Shaper | null>(null);
  const [allowRestart, setAllowRestart] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const editable = can("change_qos") && can("apply_config");

  useEffect(() => { if (data?.shaper && !form) setForm(data.shaper); }, [data, form]);

  const save = async () => {
    if (!form || !window.confirm("Apply the shaper settings? accel-ppp reloads; health-gated with automatic rollback.")) return;
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await put<{ result: string }>("/api/qos/config", { shaper: form, allow_restart: allowRestart });
      setMsg(r.result);
      reload();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const f = (k: keyof Shaper, v: unknown) => form && setForm({ ...form, [k]: v });
  return (
    <>
      <PageHeader title="QoS — plan rates from RADIUS" />
      <ErrorNote error={error} />
      <div className="grid gap-3 xl:grid-cols-2">
        <Card title="Rate guard">
          {data ? <CheckList checks={[data.check]} /> : <Empty />}
          <p className="mt-2 text-[11px] text-zinc-500">
            Priority = each subscriber's plan rate, sent by Jaze in the Access-Accept (and changed live by CoA), enforced per
            session by accel-ppp (tbf download / police upload on pppN). Send rates in <b>M</b>, never <b>G</b>: accel-ppp 1.14.0 reads 1G as 10 Gbit.
          </p>
        </Card>
        <Card title="Active sessions by plan">
          {data && Object.keys(data.plans).length ? (
            <table className={T.table}>
              <thead><tr><th className={T.th}>Download / upload</th><th className={T.th}>accel value (kbit)</th><th className={`${T.th} text-right`}>Sessions</th></tr></thead>
              <tbody>
                {Object.entries(data.plans).sort((a, b) => b[1] - a[1]).map(([rate, n]) => (
                  <tr key={rate} className={T.tr}>
                    <td className={T.td}>{rate === "unshaped" ? <Badge tone="warn">no RADIUS rate</Badge> : rate.split("/").map((k) => fmtKbit(Number(k))).join(" / ")}</td>
                    <td className={`${T.td} font-mono text-zinc-500`}>{rate}</td>
                    <td className={`${T.td} num text-right`}>{fmtNum(n)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No active sessions</Empty>}
        </Card>
        <Card title="Shaper settings" className="xl:col-span-2">
          {!form ? <Empty>Shaper not configured</Empty> : (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                {([["attr", "RADIUS attribute"], ["vendor", "Vendor (dictionary)"]] as const).map(([k, label]) => (
                  <label key={k} className="text-[11px] text-zinc-400">{label}
                    <input className={`${inputCls} mt-1 block w-full`} disabled={!editable} value={form[k] ?? ""} onChange={(e) => f(k, e.target.value || null)} />
                  </label>
                ))}
                <label className="text-[11px] text-zinc-400">Max plan rate (Mbit/s, guard)
                  <input type="number" min={1} className={`${inputCls} mt-1 block w-full`} disabled={!editable} value={form.max_rate_mbit ?? ""} onChange={(e) => f("max_rate_mbit", e.target.value ? Number(e.target.value) : null)} />
                </label>
                <label className="text-[11px] text-zinc-400">Download limiter
                  <select className={`${inputCls} mt-1 block w-full`} disabled={!editable} value={form.down_limiter} onChange={(e) => f("down_limiter", e.target.value)}>
                    {["tbf", "htb", "clsact"].map((x) => <option key={x}>{x}</option>)}
                  </select>
                </label>
                <label className="text-[11px] text-zinc-400">Upload limiter
                  <select className={`${inputCls} mt-1 block w-full`} disabled={!editable} value={form.up_limiter} onChange={(e) => f("up_limiter", e.target.value)}>
                    {["police", "htb"].map((x) => <option key={x}>{x}</option>)}
                  </select>
                </label>
                <label className="flex items-end gap-2 text-xs text-zinc-300">
                  <input type="checkbox" disabled={!editable} checked={form.require_rate} onChange={(e) => f("require_rate", e.target.checked)} />
                  Report sessions without a RADIUS rate
                </label>
              </div>
              {editable ? (
                <div className="flex items-center gap-3">
                  <Button variant="primary" disabled={busy} onClick={save}>{busy ? "Applying…" : "Apply"}</Button>
                  <Button variant="ghost" onClick={() => setForm(data?.shaper ?? null)}>Reset</Button>
                  <label className="flex items-center gap-1 text-xs text-zinc-400">
                    <input type="checkbox" checked={allowRestart} onChange={(e) => setAllowRestart(e.target.checked)} /> allow accel-ppp restart (drops all sessions)
                  </label>
                  {msg && <span className="text-xs text-emerald-400">{msg}</span>}
                </div>
              ) : <KV rows={[["Permission", "change_qos + apply_config needed to edit"]]} />}
              <ErrorNote error={err} />
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
