import { useState } from "react";
import { api } from "../api/client";
import type { Me } from "../api/types";
import { Button, ErrorNote, inputCls } from "../components/ui";

export default function Login({ onLogin }: { onLogin: (m: Me) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      onLogin(await api<Me>("/api/auth/login", { json: { username, password }, headers: { "X-Requested-With": "bng" } }));
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center">
      <form onSubmit={submit} className="w-72 space-y-3 rounded border border-zinc-800 bg-zinc-900/70 p-5">
        <div>
          <div className="text-sm font-semibold text-zinc-100">BNG Console</div>
          <div className="text-[11px] text-zinc-500">Administrator sign-in</div>
        </div>
        <label className="block text-[11px] text-zinc-400">
          Username
          <input className={`${inputCls} mt-1 w-full`} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label className="block text-[11px] text-zinc-400">
          Password
          <input className={`${inputCls} mt-1 w-full`} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <ErrorNote error={error} />
        <Button variant="primary" className="w-full" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
