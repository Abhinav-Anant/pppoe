import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, nodePath, post } from "../api/client";
import type { Version } from "../api/types";
import { useCan } from "../components/context";
import { Badge, Button, Card, Empty, ErrorNote, fmtTime, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface Validation { valid: boolean; changed: string[]; required_permissions: string[]; allowed: boolean; accel_conf_diff: string }

function Diff({ text }: { text: string }) {
  if (!text.trim()) return <Empty>No differences</Empty>;
  return (
    <pre className="max-h-[28rem] overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-[11px] leading-snug">
      {text.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "text-emerald-400" : l.startsWith("-") && !l.startsWith("---") ? "text-red-400" : l.startsWith("@@") ? "text-sky-400" : "text-zinc-400"}>{l || " "}</div>
      ))}
    </pre>
  );
}

function Editor() {
  const can = useCan();
  const cur = useApi<{ version: number | null; yaml: string }>("/api/config");
  const [text, setText] = useState<string | null>(null);
  const [val, setVal] = useState<Validation | null>(null);
  const [allowRestart, setAllowRestart] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { if (cur.data && text === null) setText(cur.data.yaml); }, [cur.data, text]);
  const dirty = text !== null && cur.data && text !== cur.data.yaml;

  const validate = async () => {
    setBusy(true); setErr(null); setMsg(null); setVal(null);
    try { setVal(await post<Validation>("/api/config/validate", { yaml: text })); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const apply = async () => {
    if (!window.confirm(`Apply this configuration (${val?.changed.join(", ") || "no section changes"})?\nValidate → backup → reload → health check; automatic rollback on failure.`)) return;
    setBusy(true); setErr(null);
    try {
      const r = await post<{ result: string }>("/api/config/apply", { yaml: text, allow_restart: allowRestart });
      setMsg(r.result); setVal(null); setText(null); cur.reload();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card title={`config.yaml ${cur.data?.version ? `(active: v${cur.data.version})` : ""}`}
        actions={<>
          {dirty && <Badge tone="warn">modified</Badge>}
          <Button variant="ghost" disabled={!dirty} onClick={() => (setText(cur.data?.yaml ?? ""), setVal(null))}>Revert</Button>
          <Button disabled={busy || text === null} onClick={validate}>Validate</Button>
        </>}>
        <textarea
          spellCheck={false}
          className="h-[36rem] w-full resize-y rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-[11px] leading-snug text-zinc-200 outline-none focus:border-sky-700"
          value={text ?? ""}
          onChange={(e) => (setText(e.target.value), setVal(null))}
        />
        <p className="mt-1 text-[11px] text-zinc-500">Secrets are not part of this file (RADIUS secret: /etc/bng-platform/secrets, root only).</p>
      </Card>
      <Card title="Validation & apply">
        <ErrorNote error={err ?? cur.error} />
        {msg && <div className="mb-2 text-xs text-emerald-400">{msg}</div>}
        {!val ? <Empty>Edit, then Validate to see the generated accel-ppp.conf diff and the permissions needed.</Empty> : (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge tone="ok">valid</Badge>
              <span className="text-zinc-400">changed:</span> {val.changed.length ? val.changed.map((c) => <Badge key={c}>{c}</Badge>) : <span className="text-zinc-500">nothing</span>}
              <span className="text-zinc-400">needs:</span> {val.required_permissions.map((p) => <Badge key={p} tone={can(p) ? "ok" : "bad"}>{p}</Badge>)}
            </div>
            <div className="text-[11px] text-zinc-500">accel-ppp.conf diff</div>
            <Diff text={val.accel_conf_diff} />
            <div className="flex items-center gap-3">
              <Button variant="primary" disabled={busy || !val.allowed || !dirty} onClick={apply}>{busy ? "Applying…" : "Apply"}</Button>
              <label className="flex items-center gap-1 text-xs text-zinc-400">
                <input type="checkbox" checked={allowRestart} onChange={(e) => setAllowRestart(e.target.checked)} />
                allow accel-ppp restart / PPPoE interface removal (disconnects subscribers)
              </label>
            </div>
            {!val.allowed && <div className="text-xs text-red-400">Your role lacks a permission this change needs.</div>}
          </div>
        )}
      </Card>
    </div>
  );
}

function History() {
  const can = useCan();
  const { data, error, reload } = useApi<Version[]>("/api/config/history");
  const [sel, setSel] = useState<number | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [allowRestart, setAllowRestart] = useState(false);

  useEffect(() => {
    if (sel == null) return;
    setDiff(null);
    get<string>(`/api/config/versions/${sel}/diff`).then(setDiff).catch((e) => setErr(e.message));
  }, [sel]);

  const rollback = async (v: number) => {
    if (!window.confirm(`Roll back to version ${v}? This applies it as a new version (health-gated).`)) return;
    setErr(null);
    try {
      const r = await post<{ result: string }>("/api/config/rollback", { version: v, allow_restart: allowRestart });
      setMsg(r.result); reload();
    } catch (e) { setErr((e as Error).message); }
  };

  const versions = [...(data ?? [])].reverse();
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card title="Versions" actions={can("rollback_config") && (
        <label className="flex items-center gap-1 text-[11px] text-zinc-400">
          <input type="checkbox" checked={allowRestart} onChange={(e) => setAllowRestart(e.target.checked)} /> allow restart
        </label>)}>
        <ErrorNote error={err ?? error} />
        {msg && <div className="mb-2 text-xs text-emerald-400">{msg}</div>}
        <div className="max-h-[36rem] overflow-auto">
          <table className={T.table}>
            <thead><tr>{["Version", "Time", "Admin", "Source", ""].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
            <tbody>
              {versions.map((v, i) => (
                <tr key={v.version} className={`${T.tr} cursor-pointer ${sel === v.version ? "bg-zinc-800/60" : ""}`} onClick={() => setSel(v.version)}>
                  <td className={`${T.td} num`}>v{v.version} {i === 0 && <Badge tone="ok">active</Badge>} {v.restart && <Badge tone="warn">restart</Badge>}</td>
                  <td className={T.td}>{fmtTime(v.timestamp)}</td>
                  <td className={T.td}>{v.admin}</td>
                  <td className={`${T.td} font-mono text-zinc-400`}>{v.source}</td>
                  <td className={`${T.td} text-right`} onClick={(e) => e.stopPropagation()}>
                    <a className="mr-2 text-sky-400 hover:underline" href={nodePath(`/api/config/versions/${v.version}`)} download>download</a>
                    {can("rollback_config") && i > 0 && <Button className="px-1.5 py-0" onClick={() => rollback(v.version)}>Roll back</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && !data.length && <Empty>No versions yet</Empty>}
        </div>
      </Card>
      <Card title={sel ? `v${sel} vs v${sel - 1} (config.yaml)` : "Diff"}>
        {sel == null ? <Empty>Select a version</Empty> : diff == null ? <Empty>Loading…</Empty> : <Diff text={diff} />}
      </Card>
    </div>
  );
}

export default function Configuration({ tab = "edit" }: { tab?: "edit" | "history" }) {
  const cls = (t: string) => `rounded px-2 py-1 text-xs ${tab === t ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`;
  return (
    <>
      <PageHeader title="Configuration">
        <Link className={cls("edit")} to="/configuration">Edit</Link>
        <Link className={cls("history")} to="/configuration/history">History</Link>
      </PageHeader>
      {tab === "edit" ? <Editor /> : <History />}
    </>
  );
}
