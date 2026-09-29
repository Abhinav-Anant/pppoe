import { useState } from "react";
import { patch, post } from "../api/client";
import { ROLES, type User } from "../api/types";
import { useAuth } from "../components/context";
import { Badge, Button, Card, ErrorNote, fmtTime, inputCls, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

export default function Users() {
  const { me } = useAuth();
  const { data, error, reload } = useApi<User[]>("/api/users");
  const [form, setForm] = useState({ username: "", password: "", role: "read_only" });
  const [err, setErr] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); reload(); } catch (e) { setErr((e as Error).message); }
  };
  const create = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => { await post("/api/users", form); setForm({ username: "", password: "", role: "read_only" }); });
  };
  const resetPassword = (u: User) => {
    const pw = window.prompt(`New password for ${u.username} (min 12 characters). Their other sessions end.`);
    if (pw) run(() => patch(`/api/users/${u.username}`, { password: pw }));
  };

  return (
    <>
      <PageHeader title="Administrators" />
      <ErrorNote error={err ?? error} />
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Accounts" className="xl:col-span-2">
          <table className={T.table}>
            <thead><tr>{["User", "Role", "Status", "Last login", "Created", ""].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
            <tbody>
              {data?.map((u) => {
                const self = u.username === me.username;
                return (
                  <tr key={u.username} className={T.tr}>
                    <td className={T.td}>{u.username}{self && <span className="ml-1 text-[10px] text-sky-400">you</span>}</td>
                    <td className={T.td}>
                      <select className={inputCls} disabled={self} value={u.role} onChange={(e) => run(() => patch(`/api/users/${u.username}`, { role: e.target.value }))}>
                        {ROLES.map((r) => <option key={r}>{r}</option>)}
                      </select>
                    </td>
                    <td className={T.td}><Badge tone={u.disabled ? "bad" : "ok"}>{u.disabled ? "disabled" : "active"}</Badge></td>
                    <td className={T.td}>{fmtTime(u.last_login_at)}</td>
                    <td className={T.td}>{fmtTime(u.created_at)}</td>
                    <td className={`${T.td} text-right`}>
                      <Button className="mr-1 px-1.5 py-0" onClick={() => resetPassword(u)}>Reset password</Button>
                      {!self && <Button variant={u.disabled ? "default" : "danger"} className="px-1.5 py-0"
                        onClick={() => run(() => patch(`/api/users/${u.username}`, { disabled: !u.disabled }))}>{u.disabled ? "Enable" : "Disable"}</Button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
        <Card title="New administrator">
          <form onSubmit={create} className="space-y-2">
            <input className={`${inputCls} w-full`} placeholder="username" autoComplete="off" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            <input className={`${inputCls} w-full`} type="password" placeholder="password (min 12)" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            <select className={`${inputCls} w-full`} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {ROLES.map((r) => <option key={r}>{r}</option>)}
            </select>
            <Button variant="primary" disabled={!form.username || form.password.length < 12}>Create</Button>
          </form>
          <table className={`${T.table} mt-4`}>
            <tbody>
              {[["super_admin", "everything incl. users"], ["network_admin", "all config, sessions, logs"], ["noc_operator", "view + disconnect sessions, logs"], ["read_only", "view"]].map(([r, d]) => (
                <tr key={r}><td className={`${T.td} text-zinc-300`}>{r}</td><td className={`${T.td} text-zinc-500`}>{d}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}
